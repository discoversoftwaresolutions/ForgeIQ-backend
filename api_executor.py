# forgeiq/api_executor.py

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from forgeiq.tasks.agent_tasks import run_codex_task

router = APIRouter()

class CodegenTask(BaseModel):
    task_id: str
    prompt: str

@router.post("/run-codegen")
def trigger_codex_task(task: CodegenTask):
    try:
        celery_task = run_codex_task.delay(task.task_id, task.prompt)
        return {"status": "queued", "task_id": task.task_id, "celery_id": celery_task.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to enqueue task: {str(e)}")
