# app/realtime.py
import os
import pusher
import datetime
from typing import Dict, Any, Optional

PUSHER_APP_ID = os.getenv("PUSHER_APP_ID", "forgeiq-app")
PUSHER_KEY = os.getenv("PUSHER_APP_KEY")
PUSHER_SECRET = os.getenv("PUSHER_APP_SECRET")
SOKETI_HOST = os.getenv("SOKETI_HOST", "soketi-forgeiq-production.up.railway.app")

if not (PUSHER_KEY and PUSHER_SECRET):
    raise RuntimeError("Missing PUSHER_APP_KEY / PUSHER_APP_SECRET envs for Soketi.")

pusher_client = pusher.Pusher(
    app_id=PUSHER_APP_ID,
    key=PUSHER_KEY,
    secret=PUSHER_SECRET,
    host=SOKETI_HOST,
    port=443,
    ssl=True,
)

def channel_for_task(task_id: Optional[str]) -> str:
    # Public channel; you can switch to `private-`/`presence-` later
    return f"public-forgeiq.{task_id}" if task_id else "public-forgeiq"

def emit_task_update(task_id: str, *, status: str, progress: int = 0,
                     current_stage: Optional[str] = None,
                     logs: Optional[str] = None,
                     details: Optional[Dict[str, Any]] = None) -> None:
    payload = {
        "task_id": task_id,
        "status": status,
        "progress": progress,
        "current_stage": current_stage,
        "logs": logs,
        "details": details or {},
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }
    pusher_client.trigger(channel_for_task(task_id), "task-update", payload)
