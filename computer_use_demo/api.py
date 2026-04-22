import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sse_starlette.sse import EventSourceResponse

from computer_use_demo.database import get_db, init_db
from computer_use_demo.env import load_env
from computer_use_demo.models import AgentMessage, AgentSession
from computer_use_demo.session_manager import session_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_env()
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


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


@app.post("/sessions")
async def create_session(db: AsyncSession = Depends(get_db)):
    session_id = uuid.uuid4().hex
    info = await session_manager.start_session_infra(session_id)

    sess = AgentSession(
        id=session_id,
        display_num=1,
        novnc_port=info["novnc_port"],
        container_id=info["container_id"],
        status="running",
    )
    db.add(sess)
    await db.commit()

    return info  # already a plain dict – fully JSON-serialisable


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


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str, db: AsyncSession = Depends(get_db)):
    await session_manager.cleanup_session(session_id)
    result = await db.execute(select(AgentSession).where(AgentSession.id == session_id))
    sess = result.scalar_one_or_none()
    if sess:
        sess.status = "stopped"
        await db.commit()
    return {"status": "stopped"}


# ---------------------------------------------------------------------------
# Chat history
# ---------------------------------------------------------------------------


@app.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AgentMessage)
        .where(AgentMessage.session_id == session_id)
        .order_by(AgentMessage.id.asc())
    )
    return [
        {"role": m.role, "content": m.get_content()} for m in result.scalars().all()
    ]


# ---------------------------------------------------------------------------
# Sending a message → triggers the agent runner
# ---------------------------------------------------------------------------


@app.post("/sessions/{session_id}/message")
async def send_message(
    session_id: str,
    req: MessageRequest,
    db: AsyncSession = Depends(get_db),
):
    # 1. Persist the user message
    msg_content = [{"type": "text", "text": req.message}]
    msg = AgentMessage(
        session_id=session_id,
        role="user",
        content=json.dumps(msg_content),
    )
    db.add(msg)
    await db.commit()

    # 2. Pull session env
    result = await db.execute(select(AgentSession).where(AgentSession.id == session_id))
    sess = result.scalar_one_or_none()
    if not sess:
        return JSONResponse(status_code=404, content={"error": "Session not found"})

    env = os.environ.copy()
    # Runner executes inside the session container (its own DISPLAY/Xvfb).
    env["DISPLAY_NUM"] = "1"
    env["DISPLAY"] = ":1"
    env["WIDTH"] = os.environ.get("WIDTH", "1024")
    env["HEIGHT"] = os.environ.get("HEIGHT", "768")
    # Always forward the API key explicitly so the subprocess has it (including .env-loaded values)
    env["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY", "")
    env["DATABASE_URL"] = os.environ.get("DATABASE_URL", "")
    if os.environ.get("MODEL"):
        env["MODEL"] = os.environ["MODEL"]
    if os.environ.get("API_PROVIDER"):
        env["API_PROVIDER"] = os.environ["API_PROVIDER"]
    if os.environ.get("TOOL_VERSION"):
        env["TOOL_VERSION"] = os.environ["TOOL_VERSION"]
    if os.environ.get("MAX_TOKENS"):
        env["MAX_TOKENS"] = os.environ["MAX_TOKENS"]
    if os.environ.get("ONLY_N_IMAGES"):
        env["ONLY_N_IMAGES"] = os.environ["ONLY_N_IMAGES"]

    # 3. Spawn runner as a background task so the HTTP response returns immediately
    asyncio.create_task(_run_agent(session_id, env))

    return {"status": "ok", "message": "Agent started"}


async def _run_agent(session_id: str, env: dict):
    """Execute runner in the session container; forward JSON-line output to the session queue."""
    queue = session_manager.get_queue(session_id)

    await queue.put(
        json.dumps(
            {
                "type": "status",
                "data": {"message": f"Agent starting for session {session_id[:8]}…"},
            }
        )
    )

    try:
        await session_manager.exec_runner(session_id=session_id, env=env)
    except Exception as exc:
        await queue.put(json.dumps({"type": "error", "data": {"error": str(exc)}}))

    await queue.put(
        json.dumps(
            {
                "type": "status",
                "data": {"message": "Agent finished."},
            }
        )
    )
    # Sentinel so the SSE generator knows the run is done
    await queue.put(None)


# ---------------------------------------------------------------------------
# SSE stream – passive: just drains the session queue
# ---------------------------------------------------------------------------


@app.get("/sessions/{session_id}/stream")
async def stream_session(session_id: str):
    queue = session_manager.get_queue(session_id)

    async def event_generator():
        # Send a heartbeat immediately so the browser sees the connection open
        yield {"event": "connected", "data": json.dumps({"session_id": session_id})}

        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=30)
            except asyncio.TimeoutError:
                # Keep-alive ping so the browser doesn't close the connection
                yield {"event": "ping", "data": "{}"}
                continue

            if item is None:
                # Runner finished – don't close; agent may be called again
                continue

            yield {"data": item}

    return EventSourceResponse(event_generator())
