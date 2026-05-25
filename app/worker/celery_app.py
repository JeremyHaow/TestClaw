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
    task_soft_time_limit=settings.AGENT_TASK_SOFT_TIME_LIMIT_SECONDS,
    task_time_limit=settings.AGENT_TASK_TIME_LIMIT_SECONDS,
    worker_concurrency=4,
)
