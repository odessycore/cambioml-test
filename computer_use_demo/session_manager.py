import asyncio
import os
import signal
from typing import Dict

class SessionManager:
    def __init__(self):
        # Map session_id to process handles and display info
        self.sessions: Dict[str, dict] = {}
        # Keep track of active display numbers to avoid collisions
        self.active_displays = set()
        self.display_offset = 10 # Start at DISPLAY=:10

    def get_available_display(self) -> int:
        for i in range(10): # Allow 10 concurrent sessions (ports 6080-6089)
            disp = self.display_offset + i
            if disp not in self.active_displays:
                return disp
        raise RuntimeError("No available displays for new sessions.")

    async def start_session_infra(self, session_id: str) -> dict:
        display_num = self.get_available_display()
        self.active_displays.add(display_num)
        
        vnc_port = 5900 + display_num
        novnc_port = 6080 + (display_num - 10)
        
        # Display Prefix
        env = os.environ.copy()
        env["DISPLAY"] = f":{display_num}"
        env["WIDTH"] = "1024"
        env["HEIGHT"] = "768"

        processes = []

        # Start Xvfb
        xvfb = await asyncio.create_subprocess_exec(
            "Xvfb", f":{display_num}", "-screen", "0", "1024x768x24",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        processes.append(xvfb)
        
        # Give Xvfb a moment to start
        await asyncio.sleep(1)

        # Start Desktop Environment (Mutter & Tint2)
        mutter = await asyncio.create_subprocess_exec(
            "mutter", "--x11", f"--display=:{display_num}",
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        processes.append(mutter)

        tint2 = await asyncio.create_subprocess_exec(
            "tint2", "-c", os.path.expanduser("~/.config/tint2/tint2rc"),
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        processes.append(tint2)

        # Start x11vnc
        vnc = await asyncio.create_subprocess_exec(
            "x11vnc", "-display", f":{display_num}", "-rfbport", str(vnc_port), "-forever", "-shared",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        processes.append(vnc)

        # Start websockify for noVNC
        novnc_path = "/opt/noVNC/utils/websockify/run"
        # Inside the container, this script translates the TCP vnc port to websocket
        websockify = await asyncio.create_subprocess_exec(
            novnc_path, str(novnc_port), f"localhost:{vnc_port}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        processes.append(websockify)

        session_info = {
            "session_id": session_id,
            "display_num": display_num,
            "vnc_port": vnc_port,
            "novnc_port": novnc_port,
            "processes": processes
        }
        self.sessions[session_id] = session_info
        return session_info

    async def cleanup_session(self, session_id: str):
        if session_id in self.sessions:
            info = self.sessions[session_id]
            for proc in info["processes"]:
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

session_manager = SessionManager()
