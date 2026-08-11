"""Reset non-queued runs and dispatch a bounded batch to celery.

Usage: python reset_and_dispatch.py [--limit N]   (default 100)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.collection import CrawlRun, CollectionStatus
from app.tasks.collection import run_ingestion


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=100, help="max runs to dispatch (default 100)"
    )
    args = parser.parse_args()
    limit = max(1, args.limit)

    engine = create_engine(settings.database_url)
    with Session(engine) as session:
        resettable = (
            CollectionStatus.FAILED,
            CollectionStatus.RUNNING,
            CollectionStatus.PARTIAL,
        )
        result = session.execute(
            update(CrawlRun)
            .where(CrawlRun.status.in_(resettable))
            .values(
                status=CollectionStatus.QUEUED,
                claim_token=None,
                started_at=None,
                completed_at=None,
                error_code=None,
                error_detail=None,
                celery_task_id=None,
            )
        )
        session.commit()
        print(f"Reset {result.rowcount} failed/running runs to queued (succeeded preserved)")

        queued = session.scalars(
            select(CrawlRun)
            .where(CrawlRun.status == CollectionStatus.QUEUED)
            .order_by(CrawlRun.created_at, CrawlRun.id)
            .limit(limit)
        ).all()
        print(f"Total queued: dispatching first {len(queued)} (limit={limit})")

        dispatched = 0
        for run in queued:
            try:
                task_result = run_ingestion.delay(str(run.id))
                session.execute(
                    update(CrawlRun)
                    .where(CrawlRun.id == run.id)
                    .values(celery_task_id=str(task_result.id))
                )
                session.commit()
                dispatched += 1
            except Exception as e:
                print(f"Failed at run {run.id}: {e}")
                break
        print(f"Dispatched {dispatched} runs")


if __name__ == "__main__":
    main()
