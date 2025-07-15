from codex_client import generate_code_with_codex
import asyncio

class MCPProcessor:
    """
    Codex-enabled MCP Processor.
    Triggers autonomous build-time code generation using prompts defined by the build pipeline.
    """

    async def process(self, data):
        task_type = data.get("type")
        prompt = data.get("prompt")

        if task_type == "build" and prompt:
            generated_code = await generate_code_with_codex(prompt)
            return {
                "status": "codex_build_complete",
                "generated_code": generated_code
            }

        return {
            "status": "ignored",
            "reason": "Only build tasks with a valid prompt are processed.",
            "data": data
        }
