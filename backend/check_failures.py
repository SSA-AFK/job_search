"""Check failed runs' error_detail."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import settings


def main() -> None:
    engine = create_engine(settings.database_url)
    with Session(engine) as session:
        rows = session.execute(
            text(
                "SELECT cr.id, cr.error_code, cr.error_detail, req.query "
                "FROM crawl_runs cr JOIN collection_requests req ON cr.collection_request_id = req.id "
                "WHERE cr.status = 'failed' ORDER BY cr.id LIMIT 15"
            )
        ).all()
        print(f"Total failed (showing 15): {len(rows)}")
        for row in rows:
            print(f"\nRun {row[0]}: query='{row[3]}'")
            print(f"  error_code: {row[1]}")
            print(f"  error_detail: {(row[2] or 'None')[:300]}")


if __name__ == "__main__":
    main()
