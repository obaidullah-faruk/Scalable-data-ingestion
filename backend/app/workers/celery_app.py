from celery import Celery

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery(
    "ingestion",
    broker=settings.celery_broker_url,
    backend=settings.redis_url,
)
celery_app.conf.update(
    task_track_started=True,
    worker_enable_remote_control=False,
)
