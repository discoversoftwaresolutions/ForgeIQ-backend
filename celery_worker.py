# forgeiq/celery_worker.py

from celery import Celery

celery_app = Celery(
    "forgeiq",
    broker="redis://localhost:6379/0",    # or use env var in production
    backend="redis://localhost:6379/0"
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)
