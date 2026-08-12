from celery import Celery  # type: ignore[import-untyped]
from celery.schedules import crontab  # type: ignore[import-untyped]

from app.core.config import settings

_TASK_IMPORTS = (
    "app.tasks.collection",
    "app.tasks.entry_verification",
    "app.tasks.job_enumeration",
    "app.tasks.schedule",
    "app.tasks.expiration",
)

celery_app = Celery(
    "company_search",
    include=_TASK_IMPORTS,
)
celery_app.conf.update(
    broker_url=settings.celery_broker_url,
    result_backend=settings.celery_result_backend,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    enable_utc=True,
    timezone="Asia/Shanghai",
    task_acks_late=True,
    task_always_eager=settings.celery_task_always_eager,
    imports=_TASK_IMPORTS,
    beat_schedule={
        "enqueue-stale-companies": {
            "task": "app.tasks.schedule.enqueue_stale_companies",
            "schedule": crontab(hour=2, minute=0),
        },
        "expire-stale-job-sources": {
            "task": "app.tasks.expiration.expire_stale_job_sources",
            "schedule": crontab(hour=2, minute=0),
        },
        "redispatch-stale-queued-runs": {
            "task": "app.tasks.schedule.redispatch_stale_queued_runs",
            "schedule": crontab(minute="*/1"),
        },
    },
)
celery_app.loader.import_default_modules()
