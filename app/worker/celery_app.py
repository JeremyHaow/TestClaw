from celery import Celery

from app.config import settings

celery_app = Celery(
    "testclaw",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.worker.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    task_track_started=True,
    task_time_limit=600,
    worker_concurrency=4,
)
