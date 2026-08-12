"""Resumable four-category Tianyancha collection for ranking pilot members."""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.errors import ProviderError
from app.models import (
    Company,
    CompanyProfileField,
    CompanyRankingSignal,
    RankingCollectionRun,
    RankingPilotMember,
)
from app.rankings.gap_plan import EnrichmentCategory, plan_ranking_enrichment
from app.rankings.selection import read_ranking_candidates
from app.rankings.tianyancha.client import TianyanchaRankingClient
from app.rankings.tianyancha.projectors import project_response

COLLECTION_RULE_VERSION = "tyc-ranking-v2"


@dataclass(frozen=True)
class PilotCollectionSummary:
    companies: int
    categories_planned: int
    categories_succeeded: int
    categories_failed: int
    categories_skipped: int
    logical_calls: int
    tool_calls: int


async def collect_pilot_tianyancha(
    session: Session,
    pilot_id: UUID,
    workbook_path: str,
    *,
    client: TianyanchaRankingClient,
    as_of: date | None = None,
) -> PilotCollectionSummary:
    today = as_of or datetime.now(UTC).date()
    window_start = _three_year_window(today)
    candidates = {item.identity_hash: item for item in read_ranking_candidates(Path(workbook_path))}
    members = tuple(
        session.execute(
            select(Company, RankingPilotMember.source_identity_hash)
            .join(RankingPilotMember, RankingPilotMember.company_id == Company.id)
            .where(RankingPilotMember.pilot_id == pilot_id)
            .order_by(Company.canonical_name)
        )
    )
    counters = {
        "planned": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "logical": 0,
        "tools": 0,
    }
    for company, identity_hash in members:
        candidate = candidates.get(identity_hash)
        if candidate is None:
            continue
        fresh = _fresh_category_keys(session, pilot_id, company.id, today)
        plan = plan_ranking_enrichment(candidate, fresh_field_keys=fresh)
        counters["planned"] += len(plan.categories)
        counters["skipped"] += 4 - len(plan.categories)
        for category in plan.categories:
            run_key = _run_key(today, category)
            if _already_succeeded(session, pilot_id, company.id, category, run_key):
                counters["skipped"] += 1
                continue
            counters["logical"] += 1
            tool_count = client.tool_call_count(category)
            counters["tools"] += tool_count
            started_at = datetime.now(UTC)
            run = session.scalar(
                select(RankingCollectionRun).where(
                    RankingCollectionRun.pilot_id == pilot_id,
                    RankingCollectionRun.company_id == company.id,
                    RankingCollectionRun.category == category.value,
                    RankingCollectionRun.run_key == run_key,
                )
            )
            if run is None:
                run = RankingCollectionRun(
                    pilot_id=pilot_id,
                    company_id=company.id,
                    category=category.value,
                    run_key=run_key,
                    status="running",
                    logical_call_count=1,
                    tool_call_count=tool_count,
                    started_at=started_at,
                )
                session.add(run)
            else:
                run.status = "running"
                run.error_code = None
                run.logical_call_count += 1
                run.tool_call_count += tool_count
                run.started_at = started_at
                run.finished_at = None
            session.commit()
            stop_batch = False
            try:
                payload = await client.fetch(
                    category,
                    company.canonical_name,
                    window_start=window_start,
                    window_end=today,
                )
                response_hash = sha256(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest()
                signals = project_response(
                    category,
                    payload,
                    company_name=company.canonical_name,
                    window_start=window_start,
                )
                _persist_signals(
                    session,
                    company.id,
                    category,
                    signals,
                    response_hash=response_hash,
                    fetched_at=datetime.now(UTC),
                )
                run.status = "succeeded"
                run.response_sha256 = response_hash
                counters["succeeded"] += 1
            except ProviderError as error:
                session.rollback()
                failed_run = session.get(RankingCollectionRun, run.id)
                assert failed_run is not None
                failed_run.status = "failed"
                failed_run.error_code = error.code
                run = failed_run
                counters["failed"] += 1
                stop_batch = error.code in {
                    "tianyancha_auth_failed",
                    "tianyancha_quota_exhausted",
                }
            run.finished_at = datetime.now(UTC)
            session.commit()
            if stop_batch:
                return PilotCollectionSummary(
                    companies=len(members),
                    categories_planned=counters["planned"],
                    categories_succeeded=counters["succeeded"],
                    categories_failed=counters["failed"],
                    categories_skipped=counters["skipped"],
                    logical_calls=counters["logical"],
                    tool_calls=counters["tools"],
                )
    # Read-only resume paths still autobegin a SQLAlchemy transaction. Close it so
    # callers can immediately open the explicit scoring transaction.
    session.commit()
    return PilotCollectionSummary(
        companies=len(members),
        categories_planned=counters["planned"],
        categories_succeeded=counters["succeeded"],
        categories_failed=counters["failed"],
        categories_skipped=counters["skipped"],
        logical_calls=counters["logical"],
        tool_calls=counters["tools"],
    )


def _persist_signals(
    session: Session,
    company_id: UUID,
    category: EnrichmentCategory,
    signals: tuple[object, ...],
    *,
    response_hash: str,
    fetched_at: datetime,
) -> None:
    from app.rankings.tianyancha.contracts import ProjectedSignal

    for item in signals:
        assert isinstance(item, ProjectedSignal)
        existing = session.scalar(
            select(CompanyRankingSignal).where(
                CompanyRankingSignal.company_id == company_id,
                CompanyRankingSignal.category == category.value,
                CompanyRankingSignal.signal_key == item.signal_key,
                CompanyRankingSignal.source_fingerprint == item.source_fingerprint,
            )
        )
        if existing is not None:
            existing.fetched_at = fetched_at
            existing.expires_at = fetched_at + timedelta(days=90)
            existing.response_sha256 = response_hash
            continue
        event_at = (
            datetime.combine(item.event_date, datetime.min.time(), tzinfo=UTC)
            if item.event_date is not None
            else None
        )
        session.add(
            CompanyRankingSignal(
                company_id=company_id,
                source_document_id=None,
                category=category.value,
                signal_key=item.signal_key,
                value=item.value,
                event_date=event_at,
                source_fingerprint=item.source_fingerprint,
                response_sha256=response_hash,
                confidence=Decimal("0.900"),
                verification_status="internal_verified",
                fetched_at=fetched_at,
                expires_at=fetched_at + timedelta(days=90),
            )
        )


def _fresh_category_keys(
    session: Session,
    pilot_id: UUID,
    company_id: UUID,
    as_of: date,
) -> frozenset[str]:
    succeeded_categories = {
        category.value
        for category in EnrichmentCategory
        if _already_succeeded(
            session,
            pilot_id,
            company_id,
            category,
            _run_key(as_of, category),
        )
    }
    cache_keys = {
        "growth": "growth.material_events_3y",
        "intellectual_property": "ai.intellectual_property_3y",
        "market_validation": "market.public_proofs_3y",
        "material_risk": "risk.material_events",
    }
    categories = {
        cache_keys[item] for item in succeeded_categories if item in cache_keys
    }
    for field in session.scalars(
        select(CompanyProfileField).where(CompanyProfileField.company_id == company_id)
    ):
        profile_category = _profile_category(field.field_key)
        if profile_category is not None:
            categories.add(profile_category)
    return frozenset(categories)


def _profile_category(field_key: str) -> str | None:
    mapping = {
        "growth.material_events_3y": "growth.material_events_3y",
        "ai.intellectual_property_3y": "ai.intellectual_property_3y",
        "market.public_proofs_3y": "market.public_proofs_3y",
        "risk.material_events": "risk.material_events",
    }
    return mapping.get(field_key)


def _already_succeeded(
    session: Session,
    pilot_id: UUID,
    company_id: UUID,
    category: EnrichmentCategory,
    run_key: str,
) -> bool:
    return (
        session.scalar(
            select(RankingCollectionRun.id).where(
                RankingCollectionRun.pilot_id == pilot_id,
                RankingCollectionRun.company_id == company_id,
                RankingCollectionRun.category == category.value,
                RankingCollectionRun.run_key == run_key,
                RankingCollectionRun.status == "succeeded",
            )
        )
        is not None
    )


def _run_key(as_of: date, category: EnrichmentCategory) -> str:
    material = f"{as_of.isoformat()}:{category.value}:{COLLECTION_RULE_VERSION}"
    return sha256(material.encode()).hexdigest()


def _three_year_window(as_of: date) -> date:
    try:
        return as_of.replace(year=as_of.year - 3)
    except ValueError:
        return as_of.replace(year=as_of.year - 3, day=28)
