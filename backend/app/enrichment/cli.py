"""Run official-website enrichment for the existing company list."""

import argparse
import asyncio
import json
from pathlib import Path

from app.core.config import settings
from app.core.database import SessionLocal
from app.enrichment.official import OfficialWebsiteEnricher
from app.ingestion.production import create_runtime_components


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--report-path", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if arguments.limit is not None and arguments.limit < 1:
        parser.error("--limit must be positive")

    runtime = create_runtime_components(settings)
    session = SessionLocal()
    try:
        results = asyncio.run(
            OfficialWebsiteEnricher(session, extractor=runtime.extractor).refresh_all(
                limit=arguments.limit,
                on_result=lambda partial: _write_report(arguments.report_path, partial),
            )
        )
    finally:
        session.close()
    _write_report(arguments.report_path, results)
    return 0


def _write_report(report_path: Path, results: tuple[object, ...]) -> None:
    report_path.write_text(
        json.dumps(
            {"results": [result.__dict__ for result in results]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
