"""Persist and score the restricted AI-ranking calibration cohort."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.enrichment.official import OfficialEnrichmentResult, OfficialWebsiteEnricher
from app.ingestion.extraction.crew import Extractor
from app.models import (
    Company,
    CompanyProfileField,
    CompanyRankingSignal,
    CompanyRankingSnapshot,
    CompanyRankingSnapshotEvidence,
    CompanySource,
    RankingCollectionRun,
    RankingPilot,
    RankingPilotMember,
    VerificationStatus,
)
from app.models.enums import CompanyScale, FundingStage
from app.rankings.relevance import assess_ai_business_scope
from app.rankings.scoring import calibrate_by_stage, raw_scores_from_signals
from app.rankings.selection import (
    RankingCandidate,
    candidate_stratum,
    read_ranking_candidates,
    select_representative_sample,
)
from app.rankings.staging import CompanyStage, merge_small_stages

AI_INDUSTRY = "ai"
RULE_VERSION = "ai-long-term-v2"
_COMPONENT_FIELDS = {
    "ai_core": ("ai.core_level", "ai.products"),
    "market_validation": ("ai.market_proofs",),
    "growth_momentum": ("ai.growth_events",),
    "industry_influence": ("ai.technology_signals",),
}
_COMPONENT_MAXIMUMS = {
    "ai_core": 30,
    "market_validation": 25,
    "growth_momentum": 20,
    "industry_influence": 15,
    "reliability": 10,
}


@dataclass(frozen=True)
class PilotImportSummary:
    pilot_id: UUID
    eligible_candidates: int
    companies_created: int
    companies_matched: int
    members_selected: int


async def enrich_ai_pilot(
    session: Session, pilot_id: UUID, workbook_path: Path, *, extractor: Extractor
) -> tuple[OfficialEnrichmentResult, ...]:
    """Enrich pilot members using workbook URLs only as ephemeral crawl seeds."""
    websites = {
        candidate.identity_hash: candidate.website_candidate
        for candidate in read_ranking_candidates(workbook_path)
        if candidate.website_candidate is not None
    }
    rows = tuple(
        session.execute(
            select(Company, RankingPilotMember.source_identity_hash)
            .join(RankingPilotMember, RankingPilotMember.company_id == Company.id)
            .where(RankingPilotMember.pilot_id == pilot_id)
        )
    )
    session.expunge_all()
    session.rollback()
    enricher = OfficialWebsiteEnricher(session, extractor=extractor, max_pages_per_provider=4)
    results: list[OfficialEnrichmentResult] = []
    for company, identity_hash in rows:
        website = websites.get(identity_hash)
        if website is None:
            results.append(
                OfficialEnrichmentResult(str(company.id), company.canonical_name, "no_website", 0)
            )
            continue
        results.append(await enricher.refresh(company, website_override=website))
    return tuple(results)


def import_ai_pilot(
    session: Session,
    workbook_path: Path,
    *,
    sample_size: int = 100,
    seed: str = "ai-ranking-pilot-v1",
) -> PilotImportSummary:
    candidates = read_ranking_candidates(workbook_path)
    selected = select_representative_sample(candidates, sample_size=sample_size, seed=seed)
    digest = sha256(workbook_path.read_bytes()).hexdigest()
    created = 0
    matched = 0
    now = datetime.now(UTC)
    with session.begin():
        pilot = session.scalar(
            select(RankingPilot).where(
                RankingPilot.industry == AI_INDUSTRY,
                RankingPilot.input_sha256 == digest,
                RankingPilot.selection_seed == seed,
            )
        )
        if pilot is None:
            pilot = RankingPilot(
                industry=AI_INDUSTRY,
                input_sha256=digest,
                selection_seed=seed,
                sample_size=sample_size,
                created_at=now,
            )
            session.add(pilot)
            session.flush()
        for candidate in selected:
            company = session.scalar(
                select(Company).where(Company.normalized_name == candidate.normalized_name)
            )
            if company is None:
                company = Company(
                    canonical_name=candidate.canonical_name,
                    normalized_name=candidate.normalized_name,
                )
                session.add(company)
                session.flush()
                created += 1
            else:
                matched += 1
            company.industry = "人工智能"
            company.sub_industry = candidate.industry_major
            company.city = candidate.city if candidate.city != "未知" else None
            company.headquarters = " · ".join(
                value for value in (candidate.province, candidate.city) if value != "未知"
            ) or None
            company.founded_year = (
                candidate.established_at.year if candidate.established_at is not None else None
            )
            company.established_at = candidate.established_at
            company.province = candidate.province if candidate.province != "未知" else None
            company.district = candidate.district
            company.company_type = candidate.company_type
            company.registered_capital = candidate.registered_capital
            company.paid_in_capital = candidate.paid_in_capital
            company.industry_sector = candidate.industry_sector
            company.industry_middle = candidate.industry_middle
            company.insured_employee_count = candidate.insured_employee_count
            company.employee_report_year = candidate.employee_report_year
            company.business_scope = candidate.business_scope
            company.scale = _public_company_scale(candidate.company_size)
            company.website = candidate.website_candidate
            member = session.scalar(
                select(RankingPilotMember).where(
                    RankingPilotMember.pilot_id == pilot.id,
                    RankingPilotMember.company_id == company.id,
                )
            )
            if member is None:
                session.add(
                    RankingPilotMember(
                        pilot_id=pilot.id,
                        company_id=company.id,
                        source_row=candidate.source_row,
                        source_identity_hash=candidate.identity_hash,
                        stratum=candidate_stratum(candidate, candidates),
                        selection_reason="hamilton_stratified_sample",
                        company_size=candidate.company_size,
                        established_at=(
                            datetime.combine(
                                candidate.established_at, datetime.min.time(), tzinfo=UTC
                            )
                            if candidate.established_at is not None
                            else None
                        ),
                        insured_employee_count=candidate.insured_employee_count,
                        employee_report_year=candidate.employee_report_year,
                    )
                )
            _upsert_ai_scope_signal(session, company.id, candidate)
        session.flush()
        pilot_id = pilot.id
        _score_pilot(session, pilot_id, now=now)
    return PilotImportSummary(
        pilot_id=pilot_id,
        eligible_candidates=len(candidates),
        companies_created=created,
        companies_matched=matched,
        members_selected=len(selected),
    )


def _public_company_scale(value: str | None) -> CompanyScale:
    mapping = {
        "微型": CompanyScale.ONE_TO_49,
        "小型": CompanyScale.ONE_TO_49,
        "中型": CompanyScale.FIFTY_TO_199,
        "大型": CompanyScale.FIVE_HUNDRED_PLUS,
    }
    return mapping.get(value or "", CompanyScale.UNKNOWN)


def _score_pilot(session: Session, pilot_id: UUID, *, now: datetime) -> None:
    member_company_ids = tuple(
        session.scalars(
            select(RankingPilotMember.company_id).where(RankingPilotMember.pilot_id == pilot_id)
        )
    )
    for company_id in member_company_ids:
        _score_company(session, pilot_id, company_id, now=now)


def _upsert_ai_scope_signal(
    session: Session,
    company_id: UUID,
    candidate: RankingCandidate,
) -> None:
    assessment = assess_ai_business_scope(candidate.business_scope)
    existing = session.scalar(
        select(CompanyRankingSignal).where(
            CompanyRankingSignal.company_id == company_id,
            CompanyRankingSignal.category == "ai_relevance",
            CompanyRankingSignal.signal_key == "ai_business_scope",
        )
    )
    if not assessment.is_ai_related:
        if existing is not None:
            session.delete(existing)
        return
    fingerprint = sha256(
        f"{candidate.identity_hash}:ai-business-scope-v1".encode()
    ).hexdigest()
    value: dict[str, object] = {
        "classification": "ai_related_business_scope",
        "matched_term_count": assessment.matched_term_count,
    }
    if existing is None:
        session.add(
            CompanyRankingSignal(
                company_id=company_id,
                source_document_id=None,
                category="ai_relevance",
                signal_key="ai_business_scope",
                value=value,
                event_date=None,
                source_fingerprint=fingerprint,
                response_sha256=None,
                confidence=Decimal("0.850"),
                verification_status="internal_verified",
                fetched_at=datetime.now(UTC),
                expires_at=None,
            )
        )
    else:
        existing.value = value
        existing.source_fingerprint = fingerprint


def _score_company(session: Session, pilot_id: UUID, company_id: UUID, *, now: datetime) -> None:
    existing = session.scalar(
        select(CompanyRankingSnapshot).where(
            CompanyRankingSnapshot.pilot_id == pilot_id,
            CompanyRankingSnapshot.company_id == company_id,
            CompanyRankingSnapshot.rule_version == RULE_VERSION,
        )
    )
    fields = tuple(
        session.scalars(
            select(CompanyProfileField).where(CompanyProfileField.company_id == company_id)
        )
    )
    verified = {
        field.field_key: field.source_document_id
        for field in fields
        if field.verification_status == VerificationStatus.VERIFIED
        and field.source_document_id is not None
    }
    component_scores = {
        component: _component_score(required, maximum, verified)
        for component, required in _COMPONENT_FIELDS.items()
        for maximum in (_COMPONENT_MAXIMUMS[component],)
    }
    source_ids = set(verified.values())
    source_ids.update(
        session.scalars(
            select(CompanySource.source_document_id).where(CompanySource.company_id == company_id)
        )
    )
    component_scores["reliability"] = _COMPONENT_MAXIMUMS["reliability"] if source_ids else 0
    missing = sorted(
        field
        for required in _COMPONENT_FIELDS.values()
        for field in required
        if field not in verified
    )
    total = sum(component_scores.values())
    if existing is None:
        snapshot = CompanyRankingSnapshot(
            pilot_id=pilot_id,
            company_id=company_id,
            industry=AI_INDUSTRY,
            rule_version=RULE_VERSION,
            total_score=Decimal(total),
            component_scores=component_scores,
            raw_component_scores={},
            stage_percentiles={},
            evidence_coverage={},
            company_stage=None,
            missing_fields=missing,
            eligibility_reasons=[],
            is_eligible=bool(source_ids),
            calculated_at=now,
        )
        session.add(snapshot)
        session.flush()
    else:
        snapshot = existing
        session.execute(
            delete(CompanyRankingSnapshotEvidence).where(
                CompanyRankingSnapshotEvidence.snapshot_id == snapshot.id
            )
        )
        snapshot.total_score = Decimal(total)
        snapshot.component_scores = component_scores
        snapshot.missing_fields = missing
        snapshot.is_eligible = bool(source_ids)
        snapshot.calculated_at = now
    for source_id in sorted(source_ids, key=str):
        session.add(
            CompanyRankingSnapshotEvidence(snapshot_id=snapshot.id, source_document_id=source_id)
        )


def _component_score(required: tuple[str, ...], maximum: int, verified: dict[str, UUID]) -> int:
    return maximum if all(field in verified for field in required) else 0


def rescore_ai_pilot(session: Session, pilot_id: UUID) -> None:
    with session.begin():
        _score_stage_calibrated_pilot(session, pilot_id, now=datetime.now(UTC))


def _score_stage_calibrated_pilot(session: Session, pilot_id: UUID, *, now: datetime) -> None:
    members = tuple(
        session.scalars(select(RankingPilotMember).where(RankingPilotMember.pilot_id == pilot_id))
    )
    raw_by_company = {}
    stages: dict[str, CompanyStage] = {}
    evidence_by_company: dict[str, set[UUID]] = {}
    coverage_by_company: dict[str, dict[str, bool]] = {}
    for member in members:
        company_key = str(member.company_id)
        profile_fields = tuple(
            session.scalars(
                select(CompanyProfileField).where(
                    CompanyProfileField.company_id == member.company_id
                )
            )
        )
        verified_profile_keys = {
            field.field_key
            for field in profile_fields
            if field.verification_status == VerificationStatus.VERIFIED
            and field.source_document_id is not None
        }
        signals = tuple(
            session.scalars(
                select(CompanyRankingSignal).where(
                    CompanyRankingSignal.company_id == member.company_id
                )
            )
        )
        company = session.get(Company, member.company_id)
        if company is not None:
            financing_labels = [
                signal.value.get("round")
                for signal in sorted(
                    signals,
                    key=lambda signal: signal.event_date or datetime.min.replace(tzinfo=UTC),
                    reverse=True,
                )
                if signal.signal_key == "financing"
                and signal.verification_status == "internal_verified"
            ]
            company.funding_stage = next(
                (
                    stage.value
                    for label in financing_labels
                    if isinstance(label, str)
                    and (stage := _funding_stage_from_label(label)) is not FundingStage.UNKNOWN
                ),
                FundingStage.UNKNOWN.value,
            )
        successful_categories = set(
            session.scalars(
                select(RankingCollectionRun.category).where(
                    RankingCollectionRun.company_id == member.company_id,
                    RankingCollectionRun.status == "succeeded",
                )
            )
        )
        raw_by_company[company_key] = raw_scores_from_signals(
            [signal.signal_key for signal in signals], verified_profile_keys
        )
        stages[company_key] = _stage_from_member(member, now.date())
        evidence_by_company[company_key] = {
            field.source_document_id
            for field in profile_fields
            if field.source_document_id is not None
            and field.verification_status == VerificationStatus.VERIFIED
        }
        signal_keys = [signal.signal_key for signal in signals]
        coverage_by_company[company_key] = {
            "ai_core": "ai.core_level" in verified_profile_keys
            or any(
                key in {
                    "ai_business_scope",
                    "ai_invention_patent",
                    "ai_software_copyright",
                }
                for key in signal_keys
            ),
            "market_validation": "ai.market_proofs" in verified_profile_keys
            or "market_validation" in successful_categories,
            "growth_momentum": "ai.growth_events" in verified_profile_keys
            or "growth" in successful_categories,
            "industry_influence": "ai.technology_signals" in verified_profile_keys
            or "intellectual_property" in successful_categories,
            "reliability": bool(evidence_by_company[company_key])
            or "material_risk" in successful_categories,
        }
    stages = merge_small_stages(stages)
    calibrated = calibrate_by_stage(raw_by_company, stages)
    for member in members:
        company_key = str(member.company_id)
        result = calibrated[company_key]
        coverage = coverage_by_company[company_key]
        eligibility_reasons = []
        if not coverage["ai_core"]:
            eligibility_reasons.append("missing_automatic_ai_relevance_evidence")
        if sum(coverage.values()) < 4:
            eligibility_reasons.append("insufficient_component_coverage")
        snapshot = session.scalar(
            select(CompanyRankingSnapshot).where(
                CompanyRankingSnapshot.pilot_id == pilot_id,
                CompanyRankingSnapshot.company_id == member.company_id,
                CompanyRankingSnapshot.rule_version == RULE_VERSION,
            )
        )
        assert snapshot is not None
        session.execute(
            delete(CompanyRankingSnapshotEvidence).where(
                CompanyRankingSnapshotEvidence.snapshot_id == snapshot.id
            )
        )
        raw = raw_by_company[company_key]
        snapshot.total_score = Decimal(result.total)
        snapshot.component_scores = result.component_scores
        snapshot.raw_component_scores = {component.value: value for component, value in raw.items()}
        snapshot.stage_percentiles = result.percentiles
        snapshot.evidence_coverage = coverage
        snapshot.company_stage = stages[company_key].value
        snapshot.missing_fields = sorted(key for key, present in coverage.items() if not present)
        snapshot.eligibility_reasons = eligibility_reasons
        snapshot.is_eligible = not eligibility_reasons
        snapshot.calculated_at = now
        for source_id in sorted(evidence_by_company[company_key], key=str):
            session.add(
                CompanyRankingSnapshotEvidence(
                    snapshot_id=snapshot.id, source_document_id=source_id
                )
            )


def _stage_from_member(member: RankingPilotMember, as_of: date) -> CompanyStage:
    established_at = member.established_at
    if established_at is None:
        return CompanyStage.GROWTH
    age_days = max(0, (as_of - established_at.date()).days)
    employees = member.insured_employee_count or 0
    if age_days < 3 * 365 and employees < 100:
        return CompanyStage.EARLY
    if age_days >= 10 * 365 or employees >= 500:
        return CompanyStage.MATURE
    return CompanyStage.GROWTH


def _funding_stage_from_label(label: str) -> FundingStage:
    normalized = label.strip().lower().replace(" ", "")
    if normalized in {"种子轮", "种子"}:
        return FundingStage.SEED
    if normalized in {"天使轮", "天使"}:
        return FundingStage.ANGEL
    if normalized.startswith("pre-a"):
        return FundingStage.PRE_A
    if "a" in normalized and "轮" in normalized and "pre-b" not in normalized:
        return FundingStage.SERIES_A
    if normalized.startswith("pre-b") or ("b" in normalized and "轮" in normalized):
        return FundingStage.SERIES_B
    if any(value in normalized for value in ("c轮", "d轮", "e轮", "f轮")):
        return FundingStage.SERIES_C_PLUS
    if any(value in normalized for value in ("ipo", "上市")):
        return FundingStage.PUBLIC
    return FundingStage.UNKNOWN


def pilot_report(session: Session, pilot_id: UUID) -> list[dict[str, object]]:
    """Return a local-only calibration report without vendor-origin fields."""
    rows = session.execute(
        select(Company, CompanyRankingSnapshot)
        .join(RankingPilotMember, RankingPilotMember.company_id == Company.id)
        .join(
            CompanyRankingSnapshot,
            (CompanyRankingSnapshot.company_id == Company.id)
            & (CompanyRankingSnapshot.pilot_id == RankingPilotMember.pilot_id),
        )
        .where(RankingPilotMember.pilot_id == pilot_id)
        .where(CompanyRankingSnapshot.rule_version == RULE_VERSION)
        .order_by(CompanyRankingSnapshot.total_score.desc(), Company.canonical_name)
    )
    return [
        {
            "company_id": str(company.id),
            "company_name": company.canonical_name,
            "total_score": str(snapshot.total_score),
            "component_scores": snapshot.component_scores,
            "raw_component_scores": snapshot.raw_component_scores,
            "stage_percentiles": snapshot.stage_percentiles,
            "company_stage": snapshot.company_stage,
            "evidence_coverage": snapshot.evidence_coverage,
            "missing_fields": snapshot.missing_fields,
            "eligibility_reasons": snapshot.eligibility_reasons,
            "is_eligible": snapshot.is_eligible,
            "rule_version": snapshot.rule_version,
        }
        for company, snapshot in rows
    ]
