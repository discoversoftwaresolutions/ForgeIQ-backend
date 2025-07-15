# forgeiq/tasks/agent_tasks.py

from forgeiq.codex_client import generate_code_with_codex
from forgeiq.celery_worker import celery_app
from pathlib import Path

@celery_app.task
def run_codex_task(task_id: str, prompt: str) -> str:
    import asyncio
    result = asyncio.run(generate_code_with_codex(prompt))

    output_path = f"src/generated/{task_id}.py"
    Path("src/generated").mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(result)

    return output_path
