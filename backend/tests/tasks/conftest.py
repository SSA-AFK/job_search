import pytest

from app.tasks.celery_app import celery_app


@pytest.fixture(autouse=True)
def eager_celery_tasks():
    previous = dict(celery_app.conf)
    celery_app.conf.update(
        broker_url="memory://",
        result_backend="cache+memory://",
        task_always_eager=True,
        task_store_eager_result=False,
    )
    yield
    celery_app.conf.update(previous)
