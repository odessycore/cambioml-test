import asyncio
import os
from typing import Dict


class SessionManager:
    def __init__(self):
        # Map session_id -> process handles and display info
        self.sessions: Dict[str, dict] = {}
        # Keep track of active display numbers to avoid collisions
        self.active_displays: set = set()
        self.display_offset = 10  # Start at DISPLAY=:10
        # Per-session event queues for SSE streaming
        self.queues: Dict[str, asyncio.Queue] = {}

    def get_available_display(self) -> int:
        for i in range(10):  # Allow up to 10 concurrent sessions
            disp = self.display_offset + i
            if disp not in self.active_displays:
                return disp
        raise RuntimeError("No available displays for new sessions.")

    def get_queue(self, session_id: str) -> asyncio.Queue:
        if session_id not in self.queues:
            self.queues[session_id] = asyncio.Queue()
        return self.queues[session_id]

    async def start_session_infra(self, session_id: str) -> dict:
        display_num = self.get_available_display()
        self.active_displays.add(display_num)

        vnc_port = 5900 + display_num
        novnc_port = 6080 + (display_num - self.display_offset)

        env = os.environ.copy()
        env["DISPLAY"] = f":{display_num}"
        env["WIDTH"] = "1024"
        env["HEIGHT"] = "768"

        processes = []

        # Start Xvfb
        xvfb = await asyncio.create_subprocess_exec(
            "Xvfb",
            f":{display_num}",
            "-screen",
            "0",
            "1024x768x24",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        processes.append(xvfb)

        # Give Xvfb a moment to initialise
        await asyncio.sleep(1.5)

        # Start window manager
        mutter = await asyncio.create_subprocess_exec(
            "mutter",
            "--x11",
            f"--display=:{display_num}",
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        processes.append(mutter)

        # Start taskbar
        tint2 = await asyncio.create_subprocess_exec(
            "tint2",
            "-c",
            os.path.expanduser("~/.config/tint2/tint2rc"),
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        processes.append(tint2)

        # Start x11vnc
        await asyncio.sleep(0.5)
        vnc = await asyncio.create_subprocess_exec(
            "x11vnc",
            "-display",
            f":{display_num}",
            "-rfbport",
            str(vnc_port),
            "-forever",
            "-shared",
            "-nopw",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        processes.append(vnc)

        # Give x11vnc a moment to bind its port
        await asyncio.sleep(1.0)

        # Start noVNC (serves HTML client + websocket proxy on novnc_port)
        novnc = await asyncio.create_subprocess_exec(
            "/opt/noVNC/utils/novnc_proxy",
            "--vnc",
            f"localhost:{vnc_port}",
            "--listen",
            str(novnc_port),
            "--web",
            "/opt/noVNC",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        processes.append(novnc)

        # Store PIDs only (so the dict stays JSON-serialisable)
        self.sessions[session_id] = {
            "session_id": session_id,
            "display_num": display_num,
            "vnc_port": vnc_port,
            "novnc_port": novnc_port,
            "_procs": processes,  # internal only – never returned to FastAPI
        }

        # Create the event queue for this session
        self.get_queue(session_id)

        return {
            "session_id": session_id,
            "display_num": display_num,
            "vnc_port": vnc_port,
            "novnc_port": novnc_port,
        }

    async def cleanup_session(self, session_id: str):
        if session_id in self.sessions:
            info = self.sessions[session_id]
            for proc in info.get("_procs", []):
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

            self.active_displays.discard(info["display_num"])
            del self.sessions[session_id]

        if session_id in self.queues:
            del self.queues[session_id]


session_manager = SessionManager()
