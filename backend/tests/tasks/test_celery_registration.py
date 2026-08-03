import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).parents[2]


def test_celery_app_registers_all_task_entry_points_in_a_fresh_process() -> None:
    command = (
        "from app.tasks.celery_app import celery_app; "
        "required = {'app.tasks.collection.run_ingestion', "
        "'app.tasks.schedule.enqueue_stale_companies', "
        "'app.tasks.expiration.expire_stale_job_sources'}; "
        "assert required <= set(celery_app.tasks)"
    )

    result = subprocess.run(
        [sys.executable, "-c", command], cwd=BACKEND_DIR, capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr


def test_default_redis_result_backend_initializes_in_a_fresh_process() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.tasks.celery_app import celery_app; celery_app.backend.as_uri()",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
