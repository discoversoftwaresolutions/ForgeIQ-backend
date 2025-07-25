# File: forgeiq-backend/forgeiq_utils.py

import json
import redis.asyncio as redis
import os
import logging
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# --- Redis Client Setup for ForgeIQ ---
# Ensure this matches ForgeIQ's Redis config, possibly a different DB or instance
REDIS_URL = os.getenv("FORGEIQ_REDIS_URL", "redis://localhost:6379/1") # Use a different DB number for ForgeIQ
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

async def update_forgeiq_task_state_and_notify(
    task_id: str,
    status: str,
    logs: str = None,
    current_stage: str = None,
    progress: int = None,
    output_data: Dict = None,
    details: Dict = None
):
    from app.database import SessionLocal # Import ForgeIQ's SessionLocal
    from app.models import ForgeIQTask # Import ForgeIQ's Task model

    db = SessionLocal()
    try:
        task_obj = db.query(ForgeIQTask).filter(ForgeIQTask.id == task_id).first()
        if not task_obj:
            logger.error(f"ForgeIQTask with ID {task_id} not found in DB for update.")
            return

        if status: task_obj.status = status
        if logs:
            if task_obj.logs: task_obj.logs += f"\n[{datetime.utcnow().isoformat()}] {logs}"
            else: task_obj.logs = f"[{datetime.utcnow().isoformat()}] {logs}"
        if current_stage: task_obj.current_stage = current_stage
        if progress is not None: task_obj.progress = progress
        if output_data is not None: task_obj.output_data = output_data
        if details is not None:
            if task_obj.details: task_obj.details.update(details)
            else: task_obj.details = details
        task_obj.updated_at = datetime.utcnow()

        db.commit()

        # Publish a lightweight update to Redis Pub/Sub for WebSockets
        r = await get_forgeiq_redis_client()
        update_data = {
            "task_id": task_id,
            "status": status,
            "progress": progress,
            "current_stage": current_stage,
            "updated_at": datetime.utcnow().isoformat()
        }
        if logs: update_data["logs_snippet"] = logs
        if output_data: update_data["output_data_summary"] = output_data.get("status") # Summarize output
        if details: update_data["details_summary"] = details.get("error_type") # Summarize details

        await r.publish(f"forgeiq_task_updates:{task_id}", json.dumps(update_data))
        logger.info(f"ForgeIQ Task {task_id} DB state updated & Redis notified: Status={status}, Stage={current_stage}, Progress={progress}%")

    except Exception as e:
        db.rollback()
        logger.exception(f"Error during ForgeIQTask state update for {task_id}: {e}")
    finally:
        db.close()
