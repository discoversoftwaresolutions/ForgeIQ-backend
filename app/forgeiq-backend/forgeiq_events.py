# forgeiq_events.py
import os
import json
import time
import socket
import logging
from typing import Any, Dict, Optional, Callable
from dataclasses import dataclass, asdict

try:
    import redis  # sync client (suited for Celery workers)
except ImportError as e:
    raise RuntimeError("redis-py is required for forgeiq_events. pip install redis") from e

logger = logging.getLogger(__name__)

REDIS_CHANNEL = os.getenv("FORGEIQ_TASK_UPDATES_CHANNEL", "forgeiq_task_updates")

# ---------- Schema ----------

@dataclass
class TaskEvent:
    # identity
    event: str                 # e.g., "TaskUpdated", "BuildUpdated", "DeploymentCompleted"
    task_id: str
    task_type: str

    # status
    status: str                # "pending" | "running" | "completed" | "failed" | ...
    current_stage: str = ""
    progress: int = 0          # 0..100

    # payloads (optional, but useful for the UI)
    payload: Optional[Dict[str, Any]] = None      # original request payload
    output_data: Optional[Dict[str, Any]] = None  # results
    details: Optional[Dict[str, Any]] = None      # misc structured metadata
    logs: Optional[str] = None                    # short textual message/log line

    # observability
    timestamp: float = 0.0                         # epoch seconds
    source: str = ""                                # worker host/name

def _now() -> float:
    return time.time()

def _hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "unknown-host"

def _redis_from_env() -> "redis.Redis":
    """
    Create a sync Redis client from env:
      - REDIS_URL=redis://:password@host:port/0  (preferred)
      - or FORGEIQ_REDIS_HOST / FORGEIQ_REDIS_PORT / FORGEIQ_REDIS_DB / FORGEIQ_REDIS_PASSWORD
    """
    url = os.getenv("REDIS_URL")
    if url:
        return redis.Redis.from_url(url, decode_responses=True)
    host = os.getenv("FORGEIQ_REDIS_HOST", "localhost")
    port = int(os.getenv("FORGEIQ_REDIS_PORT", "6379"))
    db = int(os.getenv("FORGEIQ_REDIS_DB", "0"))
    pwd = os.getenv("FORGEIQ_REDIS_PASSWORD")
    return redis.Redis(host=host, port=port, db=db, password=pwd, decode_responses=True)

def _publish(r: "redis.Redis", event: TaskEvent) -> None:
    data = asdict(event)
    if not data.get("timestamp"):
        data["timestamp"] = _now()
    if not data.get("source"):
        data["source"] = _hostname()
    payload = json.dumps(data, separators=(",", ":"), default=str)
    r.publish(REDIS_CHANNEL, payload)

# ---------- Public API ----------

class TaskEmitter:
    """
    Lightweight helper for Celery tasks to publish standardized events and (optionally)
    keep the DB task row in sync via a supplied updater function.

    Example DB updater signature:
        def updater(task_id: str, **fields): ...
    """
    def __init__(self,
                 task_id: str,
                 task_type: str,
                 default_event: str = "TaskUpdated",
                 redis_client: Optional["redis.Redis"] = None,
                 db_updater: Optional[Callable[..., None]] = None):
        self.task_id = task_id
        self.task_type = task_type
        self.default_event = default_event
        self.r = redis_client or _redis_from_env()
        self.db_updater = db_updater

    # ---- Low-level ----
    def emit(self,
             status: str,
             *,
             event: Optional[str] = None,
             current_stage: str = "",
             progress: Optional[int] = None,
             logs: Optional[str] = None,
             payload: Optional[Dict[str, Any]] = None,
             output_data: Optional[Dict[str, Any]] = None,
             details: Optional[Dict[str, Any]] = None) -> None:

        ev = TaskEvent(
            event=event or self.default_event,
            task_id=self.task_id,
            task_type=self.task_type,
            status=status,
            current_stage=current_stage,
            progress=int(progress) if isinstance(progress, (int, float)) else 0,
            logs=logs,
            payload=payload,
            output_data=output_data,
            details=details,
            timestamp=_now(),
            source=_hostname(),
        )
        try:
            _publish(self.r, ev)
        except Exception as e:
            logger.warning(f"[TaskEmitter] Redis publish failed: {e}")

        # Keep DB in sync if provided
        if self.db_updater:
            fields = {
                "status": status,
                "current_stage": current_stage,
                "progress": ev.progress,
                "logs": logs,
                "output_data": output_data,
                "details": details
            }
            # Remove None values to avoid overwriting columns unintentionally
            fields = {k: v for k, v in fields.items() if v is not None}
            try:
                self.db_updater(self.task_id, **fields)
            except Exception as e:
                logger.warning(f"[TaskEmitter] DB update failed: {e}")

    # ---- Conveniences ----
    def started(self, *, stage: str = "Started", logs: Optional[str] = None, payload: Optional[Dict[str, Any]] = None):
        self.emit("running", current_stage=stage, progress=1, logs=logs, payload=payload)

    def stage(self, stage: str, *, progress: Optional[int] = None, logs: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        self.emit("running", current_stage=stage, progress=progress or 1, logs=logs, details=details)

    def progress(self, percent: int, *, stage: Optional[str] = None, logs: Optional[str] = None):
        self.emit("running", current_stage=stage or "", progress=max(0, min(100, int(percent))), logs=logs)

    def log(self, message: str, *, stage: Optional[str] = None, progress: Optional[int] = None, details: Optional[Dict[str, Any]] = None):
        self.emit("running", current_stage=stage or "", progress=progress or 0, logs=message, details=details)

    def succeeded(self, *, output: Optional[Dict[str, Any]] = None, logs: Optional[str] = None, event: Optional[str] = None):
        self.emit("completed", current_stage="Completed", progress=100, logs=logs, output_data=output, event=event)

    def failed(self, *, error_message: str, details: Optional[Dict[str, Any]] = None, event: Optional[str] = None):
        self.emit("failed", current_stage="Failed", progress=100, logs=error_message, details=details, event=event)
