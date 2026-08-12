import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.schema import MetaData

from app.core.config import settings
from app.ingestion.extraction.client import OpenAICompatibleLlmClient
from app.ingestion.extraction.crew import CrewExtractor
from app.models import *
from app.models.base import Base
from app.rankings.service import (
    enrich_ai_pilot,
    import_ai_pilot,
    pilot_report,
    rescore_ai_pilot,
)
from app.rankings.tianyancha.client import TianyanchaRankingClient
from app.rankings.tianyancha.service import collect_pilot_tianyancha


def _configured_extractor() -> CrewExtractor | None:
    if not (
        settings.openai_compatible_base_url
        and settings.openai_compatible_model
        and settings.openai_compatible_api_key
    ):
        return None
    return CrewExtractor(
        OpenAICompatibleLlmClient(
            base_url=settings.openai_compatible_base_url,
            model=settings.openai_compatible_model,
            api_key=settings.openai_compatible_api_key,
            timeout_seconds=settings.openai_request_timeout_seconds,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an internal AI ranking calibration cohort")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--workbook", required=True, type=Path)
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument("--seed", default="ai-ranking-pilot-v1")
    parser.add_argument(
        "--report", action="store_true", help="include the internal calibration report"
    )
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="collect approved first-party pages, persist evidence, and refresh scores",
    )
    parser.add_argument(
        "--collect-tyc",
        action="store_true",
        help="collect four missing internal ranking categories via Tianyancha CLI",
    )
    args = parser.parse_args()
    if args.database_url == settings.database_url:
        parser.error("--database-url must not be the configured default database")
    if not args.database_url.startswith("sqlite:///"):
        parser.error("the pilot importer accepts an explicit SQLite database URL only")
    engine = create_engine(args.database_url)
    metadata = Base.metadata
    assert isinstance(metadata, MetaData)
    metadata.create_all(engine)
    with Session(engine) as session:
        summary = import_ai_pilot(
            session, args.workbook, sample_size=args.sample_size, seed=args.seed
        )
        result: dict[str, object] = {
            "pilot_id": str(summary.pilot_id),
            "eligible_candidates": summary.eligible_candidates,
            "companies_created": summary.companies_created,
            "companies_matched": summary.companies_matched,
            "members_selected": summary.members_selected,
        }
        if args.enrich:
            extractor = _configured_extractor()
            if extractor is None:
                parser.error("extractor is not configured")
            enrichment = asyncio.run(
                enrich_ai_pilot(
                    session,
                    summary.pilot_id,
                    args.workbook,
                    extractor=extractor,
                )
            )
            rescore_ai_pilot(session, summary.pilot_id)
            result["enrichment"] = {
                "succeeded": sum(item.status == "succeeded" for item in enrichment),
                "no_website": sum(item.status == "no_website" for item in enrichment),
                "failed": sum(
                    item.status not in {"succeeded", "no_website"} for item in enrichment
                ),
            }
        if args.collect_tyc:
            collection = asyncio.run(
                collect_pilot_tianyancha(
                    session,
                    summary.pilot_id,
                    str(args.workbook),
                    client=TianyanchaRankingClient(),
                )
            )
            result["tianyancha_collection"] = {
                "companies": collection.companies,
                "categories_planned": collection.categories_planned,
                "categories_succeeded": collection.categories_succeeded,
                "categories_failed": collection.categories_failed,
                "categories_skipped": collection.categories_skipped,
                "logical_calls": collection.logical_calls,
                "tool_calls": collection.tool_calls,
            }
            rescore_ai_pilot(session, summary.pilot_id)
        if args.report:
            rescore_ai_pilot(session, summary.pilot_id)
            result["report"] = pilot_report(session, summary.pilot_id)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
