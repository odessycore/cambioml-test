import asyncio
import os
import sys
import json
from collections.abc import Callable

from anthropic.types.beta import BetaContentBlockParam, BetaMessageParam
from anthropic import APIResponseValidationError, APIStatusError, APIError

from computer_use_demo.loop import sampling_loop, APIProvider
from computer_use_demo.tools import ToolResult, ToolVersion
from computer_use_demo.models import AgentMessage
from computer_use_demo.database import AsyncSessionLocal

def _print_json(event_type: str, data: dict):
    payload = {"type": event_type, "data": data}
    print(json.dumps(payload), flush=True)

class Runner:
    def __init__(self, session_id: str):
        self.session_id = session_id

    async def fetch_history(self) -> list[BetaMessageParam]:
        from sqlalchemy import select
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AgentMessage)
                .where(AgentMessage.session_id == self.session_id)
                .order_by(AgentMessage.id.asc())
            )
            rows = result.scalars().all()
            messages = []
            for r in rows:
                content = r.get_content()
                # Ensure the content is properly typed for the loop
                messages.append({"role": r.role, "content": content})
            return messages

    async def save_message(self, role: str, content: list[BetaContentBlockParam] | str):
        async with AsyncSessionLocal() as db:
            msg = AgentMessage(
                session_id=self.session_id,
                role=role,
                content=json.dumps(content)
            )
            db.add(msg)
            await db.commit()

    async def run(self):
        messages = await self.fetch_history()
        
        def output_callback(block: BetaContentBlockParam):
            _print_json("output", block)

        def tool_output_callback(result: ToolResult, tool_id: str):
            res_dict = {"tool_id": tool_id}
            if result.output: res_dict["output"] = result.output
            if result.error: res_dict["error"] = result.error
            if result.base64_image: res_dict["base64_image"] = result.base64_image
            if result.system: res_dict["system"] = result.system
            _print_json("tool_result", res_dict)

        def api_response_callback(req, res, exc):
            if exc:
                _print_json("api_error", {"error": str(exc)})
            else:
                _print_json("api_success", {})

        provider = APIProvider(os.getenv("API_PROVIDER", "anthropic"))
        model = os.getenv("MODEL", "claude-3-5-sonnet-20241022")
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        
        # Start sampling loop
        original_len = len(messages)
        new_messages = await sampling_loop(
            model=model,
            provider=provider,
            system_prompt_suffix="",
            messages=messages,
            output_callback=output_callback,
            tool_output_callback=tool_output_callback,
            api_response_callback=api_response_callback,
            api_key=api_key,
            only_n_most_recent_images=10,
            max_tokens=4096,
            tool_version=ToolVersion("computer-20241022") # Default ToolVersion
        )
        
        # Save the new messages back to the DB
        for msg in new_messages[original_len:]:
            await self.save_message(msg["role"], msg["content"]) 

if __name__ == "__main__":
    session_id = sys.argv[1]
    _print_json("status", {"message": f"Starting runner for {session_id}"})
    runner = Runner(session_id)
    asyncio.run(runner.run())
    _print_json("status", {"message": f"Completed runner for {session_id}"})
