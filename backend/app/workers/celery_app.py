from celery import Celery

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery(
    "ingestion",
    broker=settings.celery_broker_url,
    backend=settings.redis_url,
)
celery_app.conf.update(
    imports=("app.workers.tasks",),
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_publish_retry=True,
    task_publish_retry_policy={
        "max_retries": 3,
        "interval_start": 1,
        "interval_step": 2,
        "interval_max": 10,
    },
    worker_enable_remote_control=False,
)
