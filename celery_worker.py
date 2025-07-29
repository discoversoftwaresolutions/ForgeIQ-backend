# File: forgeiq-backend/forgeiq_celery.py

import os
from celery import Celery
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO) # Ensure logging is configured for Celery app

# --- Redis URL for Celery Broker and Backend ---
# This MUST be the same FORGEIQ_REDIS_URL that your FastAPI app uses
# and that is set in Railway's environment variables for this service.
REDIS_URL = os.getenv("FORGEIQ_REDIS_URL")

if not REDIS_URL:
    # IMPORTANT: In a production Railway environment, this variable *must* be set.
    # This fallback is primarily for local development if you intend to run Redis locally.
    logger.error("FORGEIQ_REDIS_URL environment variable is NOT set for Celery. Falling back to localhost. This WILL cause connection errors if Redis is not local.")
    REDIS_URL = "redis://localhost:6379/1"
    # For a stricter production setup, you might want to raise an exception here:
    # raise ValueError("FORGEIQ_REDIS_URL environment variable not set for Celery.")


celery_app = Celery(
    "forgeiq_backend",
    broker=REDIS_URL,       # Use the environment variable for the broker
    backend=REDIS_URL,      # Use the environment variable for the backend
    include=["tasks.build_tasks"] # Ensure your task modules are included
)

# Optional configuration (add/adjust as needed)
# Example: Ensure tasks are acknowledged late, which means if worker dies,
# task goes back to queue (good for long-running tasks).
celery_app.conf.update(
    result_expires=3600, # Results expire after 1 hour
    broker_connection_retry_on_startup=True, # Attempt reconnection on startup
    task_acks_late=True, # Task is acknowledged after it's done, not before
    worker_prefetch_multiplier=1, # Don't prefetch too many tasks
    task_reject_on_worker_lost=True, # Requeue tasks if worker dies
    # Optional: Configure TLS/SSL for Redis if your Railway Redis uses `rediss://`
    # broker_use_ssl={'ssl_cert_reqs': 'required'},
    # redis_backend_use_ssl={'ssl_cert_reqs': 'required'},
)

# Ensure this app is used for tasks if defined in this file
if __name__ == '__main__':
    celery_app.start()
