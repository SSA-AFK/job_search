"""Check current run status distribution."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.collection import CrawlRun, CollectionStatus


def main() -> None:
    engine = create_engine(settings.database_url)
    with Session(engine) as session:
        results = session.execute(
            select(CrawlRun.status, func.count())
            .group_by(CrawlRun.status)
        ).all()
        print("Run status distribution:")
        for status, count in results:
            print(f"  {status.value}: {count}")

        error_dist = session.execute(
            select(CrawlRun.error_code, func.count())
            .where(CrawlRun.status == CollectionStatus.FAILED)
            .group_by(CrawlRun.error_code)
        ).all()
        print("\nFailed error codes:")
        for code, count in error_dist:
            print(f"  {code}: {count}")

        # Check if any succeeded with jobs
        succeeded_with_jobs = session.execute(
            select(func.count())
            .select_from(CrawlRun)
            .where(CrawlRun.status == CollectionStatus.SUCCEEDED, CrawlRun.jobs_written > 0)
        ).scalar()
        print(f"\nSucceeded with jobs written: {succeeded_with_jobs}")


if __name__ == "__main__":
    main()
