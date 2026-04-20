import asyncio
import uuid
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from computer_use_demo.database import init_db, get_db
from computer_use_demo.models import AgentSession, AgentMessage
from computer_use_demo.session_manager import session_manager

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class MessageRequest(BaseModel):
    message: str

@app.post("/sessions")
async def create_session(db: AsyncSession = Depends(get_db)):
    session_id = uuid.uuid4().hex
    # Spawn X11 & VNC
    info = await session_manager.start_session_infra(session_id)
    
    sess = AgentSession(
        id=session_id,
        display_num=info["display_num"],
        novnc_port=info["novnc_port"],
        status="running"
    )
    db.add(sess)
    await db.commit()
    
    return {
        "session_id": info["session_id"],
        "display_num": info["display_num"],
        "vnc_port": info["vnc_port"],
        "novnc_port": info["novnc_port"]
    }

@app.get("/sessions")
async def list_sessions(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AgentSession))
    return [s.to_dict() for s in result.scalars().all()]

@app.get("/sessions/{session_id}")
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AgentSession).where(AgentSession.id == session_id))
    sess = result.scalar_one_or_none()
    if sess:
        return sess.to_dict()
    return JSONResponse(status_code=404, content={"error": "Not Found"})

@app.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AgentMessage).where(AgentMessage.session_id == session_id).order_by(AgentMessage.id.asc())
    )
    return [{"role": m.role, "content": m.get_content()} for m in result.scalars().all()]

@app.post("/sessions/{session_id}/message")
async def send_message(session_id: str, req: MessageRequest, db: AsyncSession = Depends(get_db)):
    # Create the user message
    msg_content = [{"type": "text", "text": req.message}]
    msg = AgentMessage(
        session_id=session_id,
        role="user",
        content=json.dumps(msg_content)
    )
    db.add(msg)
    await db.commit()
    return {"status": "Message added. Please connect to /stream using SSE to receive updates."}

@app.get("/sessions/{session_id}/stream")
async def stream_session(session_id: str, db: AsyncSession = Depends(get_db)):
    """
    Spawns the runner process and streams its JSON line output to the client via SSE.
    """
    result = await db.execute(select(AgentSession).where(AgentSession.id == session_id))
    sess = result.scalar_one_or_none()
    if not sess:
        return JSONResponse(status_code=404, content={"error": "Not Found"})

    env = os.environ.copy()
    env["DISPLAY_NUM"] = str(sess.display_num)
    env["WIDTH"] = "1024"
    env["HEIGHT"] = "768"

    async def event_generator():
        # Using sys.executable to run runner.py
        process = await asyncio.create_subprocess_exec(
            "python", "-m", "computer_use_demo.runner", session_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )

        while True:
            line = await process.stdout.readline()
            if not line:
                break
            
            try:
                # The line is JSON encoded from runner.py
                text = line.decode('utf-8').strip()
                if text:
                    yield {"data": text}
            except Exception as e:
                yield {"event": "error", "data": str(e)}

        await process.wait()
        yield {"event": "close", "data": "Stream closed"}

    return EventSourceResponse(event_generator())
