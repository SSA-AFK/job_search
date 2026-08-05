"""Offline JSON command for internal coverage reporting."""

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError

from app.coverage.service import CoverageReportService


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("--as-of must be valid ISO-8601") from error
    if parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--as-of must be timezone-aware")
    return parsed.astimezone(UTC)


def _positive_hours(value: str) -> int:
    try:
        hours = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("--refresh-hours must be a positive integer") from error
    if hours < 1:
        raise argparse.ArgumentTypeError("--refresh-hours must be a positive integer")
    return hours


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Report internal job-list coverage as JSON")
    parser.add_argument("--as-of", type=_aware_datetime, default=None)
    parser.add_argument("--refresh-hours", type=_positive_hours, default=24)
    return parser


def main() -> int:
    args = _parser().parse_args()
    as_of = args.as_of if args.as_of is not None else datetime.now(UTC)
    try:
        from app.core.database import SessionLocal

        with SessionLocal() as session:
            report = CoverageReportService(session).build(
                as_of=as_of,
                refresh_window=timedelta(hours=args.refresh_hours),
            )
    except (ImportError, OSError, SQLAlchemyError):
        print("coverage report failed: database unavailable", file=sys.stderr)
        return 1

    print(json.dumps(report.model_dump(mode="json"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
