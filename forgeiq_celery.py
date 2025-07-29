# File: forgeiq-backend/forgeiq_celery.py

import os
from celery import Celery
import logging # Added logging import for consistency with other files

logger = logging.getLogger(__name__) # Added logger initialization
logger.setLevel(logging.INFO) # Set logging level

# --- Redis URL for Celery Broker and Backend ---
# Get the Redis URL directly from the environment variable.
# For a production setup, we DO NOT provide a fallback to localhost here.
# If FORGEIQ_REDIS_URL is not set, this will explicitly raise an error,
# which is good because it forces correct configuration in Railway.
REDIS_URL = os.getenv("FORGEIQ_REDIS_URL")

if not REDIS_URL:
    logger.error("FORGEIQ_REDIS_URL environment variable is NOT set for Celery. Cannot proceed without Redis broker/backend URL.")
    raise ValueError("FORGEIQ_REDIS_URL environment variable not set for Celery.")


celery_app = Celery(
    "forgeiq", # Your Celery app name (was "forgeiq_backend" in previous versions, ensure consistency if changed)
    broker=REDIS_URL,       # NOW USES THE ENVIRONMENT VARIABLE
    backend=REDIS_URL,      # NOW USES THE ENVIRONMENT VARIABLE
    # Make sure to include your task modules here if they are in subdirectories
    include=["tasks.build_tasks"] # Assuming your tasks are here, adjust if paths differ
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Added common production-friendly Celery configs:
    result_expires=3600, # Results expire after 1 hour
    broker_connection_retry_on_startup=True, # Attempt reconnection on startup
    task_acks_late=True, # Task is acknowledged after it's done, not before
    worker_prefetch_multiplier=1, # Don't prefetch too many tasks
    task_reject_on_worker_lost=True, # Requeue tasks if worker dies
    # broker_use_ssl={'ssl_cert_reqs': 'required'}, # Uncomment if your Railway Redis requires TLS (e.g., `rediss://`)
    # redis_backend_use_ssl={'ssl_cert_reqs': 'required'}, # Uncomment if your Railway Redis requires TLS
)

# This block is typically for running the worker directly from this file for local dev.
if __name__ == '__main__':
    celery_app.start()
