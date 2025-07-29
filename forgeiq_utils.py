# File: forgeiq-backend/forgeiq_utils.py

import json
import redis.asyncio as redis
import os
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session # Correct import for Session type hint

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# --- Redis Client Setup for ForgeIQ ---
REDIS_URL = os.getenv("FORGEIQ_REDIS_URL")

if not REDIS_URL:
    logger.error("FORGEIQ_REDIS_URL environment variable is NOT set for ForgeIQ utils. Cannot proceed without Redis URL.")
    raise ValueError("FORGEIQ_REDIS_URL environment variable not set for ForgeIQ utils.")

forgeiq_redis_client_instance: redis.Redis = None

async def get_forgeiq_redis_client() -> redis.Redis:
    global forgeiq_redis_client_instance
    if forgeiq_redis_client_instance is None:
        try:
            forgeiq_redis_client_instance = redis.from_url(REDIS_URL, decode_responses=True)
            await forgeiq_redis_client_instance.ping()
            logger.info("✅ ForgeIQ Redis async client initialized successfully.")
        except redis.RedisError as e:
            logger.error(f"❌ ForgeIQ Failed to connect to Redis: {e}")
            raise
    return forgeiq_redis_client_instance

# --- Task State Management Functions for ForgeIQ ---

# MODIFIED: Accepts 'db' (SQLAlchemy Session) as the first parameter
# REMOVED: Internal SessionLocal() creation and db.close()
async def update_forgeiq_task_state_and_notify(
    db: Session, # Accept the database session from the caller
    task_id: str,
    status: str,
    logs: str = None,
    current_stage: str = None,
    progress: int = None,
    output_data: Dict = None,
    details: Dict = None
):
    # Removed: from app.database import SessionLocal # Not needed here anymore
    # Removed: db = SessionLocal() # Session is passed in
    from app.models import ForgeIQTask # Import ForgeIQ's Task model
    from .api_models import ForgeIQTaskStatusResponse # Import the Pydantic model for consistency

    try:
        task_obj = db.query(ForgeIQTask).filter(ForgeIQTask.id == task_id).first()
        if not task_obj:
            logger.error(f"ForgeIQTask with ID {task_id} not found in DB for update.")
            # Do not raise here, as it would crash the task trying to update non-existent task
            return

        # Update task object with new data
        if status: task_obj.status = status
        if logs:
            if task_obj.logs: task_obj.logs += f"\n[{datetime.utcnow().isoformat()}] {logs}"
            else: task_obj.logs = f"[{datetime.utcnow().isoformat()}] {logs}"
        if current_stage: task_obj.current_stage = current_stage
        if progress is not None: task_obj.progress = progress
        if output_data is not None: 
            if isinstance(output_data, dict) and isinstance(task_obj.output_data, dict):
                task_obj.output_data.update(output_data)
            else:
                task_obj.output_data = output_data
        if details is not None:
            if isinstance(details, dict) and isinstance(task_obj.details, dict):
                task_obj.details.update(details)
            else:
                task_obj.details = details
        task_obj.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(task_obj) # Refresh to get any updated fields after commit

        # --- Publish a full update to Redis Pub/Sub for WebSockets ---
        r = await get_forgeiq_redis_client()
        
        full_update_payload = ForgeIQTaskStatusResponse(
            task_id=task_obj.id,
            task_type=task_obj.task_type,
            status=task_obj.status,
            current_stage=task_obj.current_stage,
            progress=task_obj.progress,
            logs=task_obj.logs,
            output_data=task_obj.output_data,
            details=task_obj.details
        )
        
        await r.publish("forgeiq_task_updates", full_update_payload.json()) # Publish JSON string
        logger.info(f"ForgeIQ Task {task_id} DB state updated & Redis notified on 'forgeiq_task_updates': Status={status}, Stage={current_stage}, Progress={progress}%")

    except Exception as e:
        # NOTE: db.rollback() should typically be handled by the caller (the Celery task)
        # if the session is passed in, as the caller owns the transaction.
        # However, for safety in this utility, we'll keep a rollback if a commit failed here,
        # but the task's main error handler should manage the primary transaction.
        logger.exception(f"Error during ForgeIQTask state update for {task_id}: {e}")
        raise # Re-raise to ensure the caller (task) is aware of DB update failure
    finally:
        # REMOVED: db.close() - the session is passed in, the caller is responsible for closing it
        pass
