"""Run a single company collection synchronously and report timing.

Usage:
  python run_one_company.py "字节跳动"            # create + run a new request
  python run_one_company.py --run <RUN_ID>      # run an existing queued run
"""

import asyncio
import sys
import time
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy.orm import Session  # noqa: E402

from app.collection.repository import CollectionRepository  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.normalization import normalize_name  # noqa: E402
from app.tasks.collection import build_runtime_orchestrator  # noqa: E402


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--run":
        run_id = UUID(args[1])
        setup_session = SessionLocal()
        try:
            repository = CollectionRepository(setup_session)
            run = repository.get_run(run_id)
            query = (
                repository.get_request_for_run(run).query
                if run is not None and repository.get_request_for_run(run) is not None
                else str(run_id)
            )
        finally:
            setup_session.close()
    else:
        query = args[0] if args else "字节跳动"
        normalized = normalize_name(query)

        # 1. Persist a queued request + run.
        setup_session = SessionLocal()
        try:
            repository = CollectionRepository(setup_session)
            request, run = repository.create_request(query, normalized)
            setup_session.commit()
            run_id: UUID = run.id
        finally:
            setup_session.close()

    # 2. Build the production orchestrator (distinct sessions).
    orchestrator, sessions = build_runtime_orchestrator()
    try:
        started = time.monotonic()
        result = asyncio.run(orchestrator.run(run_id))
        elapsed = time.monotonic() - started
    finally:
        for session in sessions:
            session.close()

    print("=" * 60)
    print(f"query            : {query}")
    print(f"run_id           : {run_id}")
    print(f"elapsed_seconds  : {elapsed:.3f}")
    print(f"status           : {result.status.value}")
    print(f"company_id       : {result.company_id}")
    print(f"providers        : {', '.join(result.providers_attempted) or '(none)'}")
    print(f"documents_found  : {result.documents_found}")
    print(f"jobs_found       : {result.jobs_found}")
    print(f"jobs_written     : {result.jobs_written}")
    print(f"error_code       : {result.error_code}")
    print("=" * 60)


if __name__ == "__main__":
    main()