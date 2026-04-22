import asyncio
import os
import time
from typing import Dict, Optional

import docker  # type: ignore
from docker.models.containers import Container  # type: ignore


class SessionManager:
    def __init__(self):
        # Map session_id -> container metadata
        self.sessions: Dict[str, dict] = {}
        # Per-session event queues for SSE streaming
        self.queues: Dict[str, asyncio.Queue] = {}
        self._docker = docker.from_env()
        self._self_container_id = os.environ.get("HOSTNAME")
        self._network_name: Optional[str] = None

    def _get_compose_network_name(self) -> Optional[str]:
        """
        Best-effort: attach session containers to the same network as this backend
        container so they can reach `db` via docker-compose DNS.
        """
        if self._network_name is not None:
            return self._network_name
        if not self._self_container_id:
            return None
        try:
            me: Container = self._docker.containers.get(self._self_container_id)
            networks = list((me.attrs or {}).get("NetworkSettings", {}).get("Networks", {}).keys())
            self._network_name = networks[0] if networks else None
            return self._network_name
        except Exception:
            return None

    def get_queue(self, session_id: str) -> asyncio.Queue:
        if session_id not in self.queues:
            self.queues[session_id] = asyncio.Queue()
        return self.queues[session_id]

    async def start_session_infra(self, session_id: str) -> dict:
        image = os.environ.get("SESSION_IMAGE", os.environ.get("BACKEND_IMAGE", "combioml-test-backend"))
        width = os.environ.get("WIDTH", "1024")
        height = os.environ.get("HEIGHT", "768")

        # Start a dedicated desktop stack per session inside its own container.
        # Ports are fixed inside the container (5900/6080) but published to a random host port.
        env = {
            "DISPLAY_NUM": "1",
            "DISPLAY": ":1",
            "WIDTH": width,
            "HEIGHT": height,
            # runner relies on these too
            "DATABASE_URL": os.environ.get("DATABASE_URL", ""),
            "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
            "MODEL": os.environ.get("MODEL", ""),
            "API_PROVIDER": os.environ.get("API_PROVIDER", ""),
            "TOOL_VERSION": os.environ.get("TOOL_VERSION", ""),
            "MAX_TOKENS": os.environ.get("MAX_TOKENS", ""),
            "ONLY_N_IMAGES": os.environ.get("ONLY_N_IMAGES", ""),
        }

        cmd = (
            "set -e; "
            "export DISPLAY=:${DISPLAY_NUM}; "
            "./start_all.sh; "
            "./novnc_startup.sh; "
            "tail -f /dev/null"
        )

        network_name = self._get_compose_network_name()

        def _start() -> Container:
            return self._docker.containers.run(
                image=image,
                detach=True,
                entrypoint="bash",
                command=["-lc", cmd],
                environment={k: v for k, v in env.items() if v},
                ports={"6080/tcp": None},
                labels={"computer_use.session_id": session_id},
                network=network_name,
            )

        container: Container = await asyncio.to_thread(_start)

        # Wait until noVNC is reachable and capture the published host port.
        async def _wait_ready(timeout_s: float = 25.0):
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                try:
                    await asyncio.to_thread(container.reload)
                    ports = (container.attrs or {}).get("NetworkSettings", {}).get("Ports", {}) or {}
                    binding = (ports.get("6080/tcp") or [{}])[0]
                    host_port = binding.get("HostPort")
                    if host_port:
                        # Ensure process is listening inside container too
                        res = await asyncio.to_thread(
                            container.exec_run,
                            ["bash", "-lc", "netstat -tuln | grep -q ':6080 '"],
                        )
                        if getattr(res, "exit_code", 1) == 0:
                            return int(host_port)
                except Exception:
                    pass
                await asyncio.sleep(0.5)
            raise RuntimeError("Session desktop container did not become ready in time")

        novnc_port = await _wait_ready()

        self.sessions[session_id] = {
            "session_id": session_id,
            "container_id": container.id,
            "novnc_port": novnc_port,
        }

        # Create the event queue for this session
        self.get_queue(session_id)

        return {
            "session_id": session_id,
            "novnc_port": novnc_port,
            "container_id": container.id,
        }

    async def cleanup_session(self, session_id: str):
        if session_id in self.sessions:
            info = self.sessions[session_id]
            container_id = info.get("container_id")
            if container_id:
                try:
                    container: Container = await asyncio.to_thread(
                        self._docker.containers.get, container_id
                    )
                    await asyncio.to_thread(container.stop, timeout=5)
                    await asyncio.to_thread(container.remove)
                except Exception:
                    pass
            del self.sessions[session_id]

        if session_id in self.queues:
            del self.queues[session_id]

    async def exec_runner(self, *, session_id: str, env: dict) -> int:
        """
        Execute `python -m computer_use_demo.runner <session_id>` inside the session's
        container and stream JSON-line output into the session queue.
        """
        info = self.sessions.get(session_id)
        if not info or not info.get("container_id"):
            raise RuntimeError("Session infra not running")

        queue = self.get_queue(session_id)
        container_id = info["container_id"]
        loop = asyncio.get_running_loop()

        def _run_and_stream() -> int:
            container: Container = self._docker.containers.get(container_id)
            # ensure container has latest attrs
            container.reload()
            exec_env = {k: v for k, v in env.items() if v is not None and v != ""}
            result = container.exec_run(
                ["python", "-m", "computer_use_demo.runner", session_id],
                environment=exec_env,
                stream=True,
                stdout=True,
                stderr=True,
                demux=False,
            )
            # docker-py returns ExecResult(exit_code, output) OR an object with fields
            stream = getattr(result, "output", None)
            if stream is None:
                stream = result[1] if isinstance(result, tuple) and len(result) > 1 else None
            if stream is not None:
                for chunk in stream:
                    if not chunk:
                        continue
                    line = chunk.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    asyncio.run_coroutine_threadsafe(queue.put(line), loop)

            exit_code = getattr(result, "exit_code", None)
            if exit_code is None and isinstance(result, tuple) and len(result) > 0:
                exit_code = result[0]
            return int(exit_code or 0)

        return await asyncio.to_thread(_run_and_stream)


session_manager = SessionManager()
