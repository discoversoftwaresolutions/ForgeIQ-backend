# forgeiq/tasks/agent_tasks.py

from forgeiq.celery_worker import celery_app
from forgeiq.codex_client import generate_code_with_codex
from pathlib import Path
import asyncio
import logging

logger = logging.getLogger(__name__)

@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    soft_time_limit=60,
    time_limit=90,
)
def run_codex_task(self, task_id: str, prompt: str) -> str:
    try:
        logger.info(f"🔧 Running Codex task: {task_id}")
        result = asyncio.run(generate_code_with_codex(prompt))

        output_path = f"src/generated/{task_id}.py"
        Path("src/generated").mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(result)

        logger.info(f"✅ Code written to {output_path}")
        return output_path

    except Exception as e:
        logger.error(f"❌ Codex task {task_id} failed: {e}", exc_info=True)
        raise self.retry(exc=e)
