"""
Runner script executed as a subprocess per session.
Reads message history from DB, invokes the sampling loop,
and writes JSON-line events to stdout for the API to stream.
"""

import asyncio
import json
import os
import sys

from computer_use_demo.loop import APIProvider, sampling_loop
from computer_use_demo.env import load_env
from computer_use_demo.tools import ToolResult, ToolVersion
from computer_use_demo.models import AgentMessage
from computer_use_demo.database import AsyncSessionLocal


def _emit(event_type: str, data: dict):
    """Write a single JSON event line to stdout (read by the API process)."""
    print(json.dumps({"type": event_type, "data": data}), flush=True)


async def fetch_messages(session_id: str) -> list[dict]:
    """Load full conversation history from the database."""
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .order_by(AgentMessage.id.asc())
        )
        rows = result.scalars().all()
        messages = []
        for row in rows:
            content = row.get_content()
            messages.append({"role": row.role, "content": content})
        return messages


async def save_new_messages(session_id: str, messages: list[dict], original_len: int):
    """Persist messages that were added by the sampling loop."""
    async with AsyncSessionLocal() as db:
        for msg in messages[original_len:]:
            db.add(AgentMessage(
                session_id=session_id,
                role=msg["role"],
                content=json.dumps(msg["content"]),
            ))
        await db.commit()


async def run(session_id: str):
    load_env()
    # ── Configuration (mirrors Streamlit defaults) ─────────────────────────
    api_key   = os.environ.get("ANTHROPIC_API_KEY", "")
    model     = os.environ.get("MODEL", "claude-sonnet-4-5-20250929")
    provider  = APIProvider(os.environ.get("API_PROVIDER", "anthropic"))

    # Match the CLAUDE_4_5 ModelConfig from streamlit.py:
    #   tool_version = "computer_use_20250124"
    #   max_tokens   = 16384 (1024 * 16)
    tool_version: ToolVersion = os.environ.get(   # type: ignore[assignment]
        "TOOL_VERSION", "computer_use_20250124"
    )
    max_tokens   = int(os.environ.get("MAX_TOKENS", "16384"))
    only_n_images = int(os.environ.get("ONLY_N_IMAGES", "3"))

    # ── Callbacks ──────────────────────────────────────────────────────────
    def output_callback(block):
        """Called for every assistant content block (text / tool_use / thinking)."""
        if isinstance(block, dict):
            _emit("output", block)
        else:
            _emit("output", {"type": "text", "text": str(block)})

    def tool_output_callback(result: ToolResult, tool_id: str):
        payload: dict = {"tool_id": tool_id}
        if result.output:       payload["output"]       = result.output
        if result.error:        payload["error"]        = result.error
        if result.base64_image: payload["base64_image"] = result.base64_image
        if result.system:       payload["system"]       = result.system
        _emit("tool_result", payload)

    def api_response_callback(request, response, error):
        if error:
            _emit("api_error", {"error": str(error)})

    # ── Run ────────────────────────────────────────────────────────────────
    messages = await fetch_messages(session_id)
    original_len = len(messages)

    _emit("status", {"message": f"Starting agent — {len(messages)} messages in history"})

    try:
        updated_messages = await sampling_loop(
            model=model,
            provider=provider,
            system_prompt_suffix="",
            messages=messages,
            output_callback=output_callback,
            tool_output_callback=tool_output_callback,
            api_response_callback=api_response_callback,
            api_key=api_key,
            only_n_most_recent_images=only_n_images,
            tool_version=tool_version,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        _emit("error", {"error": str(exc)})
        raise

    await save_new_messages(session_id, updated_messages, original_len)
    _emit("status", {"message": "Agent completed successfully."})


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m computer_use_demo.runner <session_id>", file=sys.stderr)
        sys.exit(1)

    session_id = sys.argv[1]
    _emit("status", {"message": f"Starting runner for {session_id}"})
    asyncio.run(run(session_id))
