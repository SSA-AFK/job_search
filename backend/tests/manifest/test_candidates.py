from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.manifest.candidates import (
    CandidateEvidenceConflict,
    CandidateImportSummary,
    UnregisteredSourceError,
    canonical_candidate_fact,
    classify_candidate_confidence,
    import_candidate_facts,
    stable_evidence_id,
)
from app.manifest.contracts import (
    AiCategory,
    CandidateDecisionStatus,
    CandidateFactInput,
    ConfidenceTier,
    SourceClass,
    SourceRegistry,
    SourceRegistryEntry,
    SourceRole,
)
from app.models import Base, CandidateFact


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


def registry_entry(
    source_class: SourceClass = SourceClass.GOVERNMENT,
) -> SourceRegistryEntry:
    return SourceRegistryEntry(
        id="official_list",
        name="Official public list",
        base_url="https://example.com/public-list",
        source_class=source_class,
        authorization_basis="Public list approved for Gate 1 rehearsal.",
        robots_policy="required",
        roles=frozenset({SourceRole.CANDIDATE_POOL}),
        requests_per_second="1.0",
    )


@pytest.fixture
def registry() -> SourceRegistry:
    return SourceRegistry(entries=(registry_entry(),))


def candidate_fact(**overrides: object) -> CandidateFactInput:
    values: dict[str, object] = {
        "source_id": "official_list",
        "source_url": "HTTPS://EXAMPLE.COM:443/public-list#entry",
        "retrieved_at": datetime(2026, 8, 6, 9, tzinfo=timezone(timedelta(hours=8))),
        "canonical_name": "Acme AI",
        "aliases": ("Acme",),
        "primary_category": AiCategory.FOUNDATION_MODELS,
        "official_website": "HTTPS://WWW.ACME.AI:443/#about",
        "recruitment_url": "https://careers.acme.ai/jobs",
        "evidence_summary": "Public company profile identifies the company and its official website.",
    }
    values.update(overrides)
    return CandidateFactInput(**values)


def test_exact_candidate_replay_is_idempotent(session: Session, registry: SourceRegistry) -> None:
    first = import_candidate_facts(session, [candidate_fact()], registry)
    stored = session.scalar(select(CandidateFact))
    assert stored is not None
    updated_at = stored.updated_at

    session.rollback()
    second = import_candidate_facts(session, [candidate_fact()], registry)

    assert first == CandidateImportSummary(created=1, replayed=0)
    assert second == CandidateImportSummary(created=0, replayed=1)
    assert session.scalar(select(func.count()).select_from(CandidateFact)) == 1
    assert session.scalar(select(CandidateFact.updated_at)) == updated_at


def test_import_normalizes_public_identity_and_derives_reviewable_confidence(
    session: Session, registry: SourceRegistry
) -> None:
    fact = candidate_fact(
        canonical_name="Ａｃｍｅ  AI",
        aliases=("Acme", "Ａｃｍｅ", "Beta AI", "Beta   AI"),
    )

    import_candidate_facts(session, [fact], registry)
    stored = session.scalar(select(CandidateFact))

    assert stored is not None
    assert stored.normalized_name == "acmeai"
    assert stored.aliases == ["Acme", "Beta AI"]
    assert stored.source_url == "https://example.com/public-list"
    assert stored.official_website == "https://www.acme.ai/"
    assert stored.retrieved_at == datetime(2026, 8, 6, 1, tzinfo=UTC)
    assert stored.decision_status is CandidateDecisionStatus.REVIEW_REQUIRED
    assert stored.confidence_tier is ConfidenceTier.HIGH
    assert stored.confidence_reason == "government source includes an official website"


def test_canonical_evidence_ignores_alias_order_and_url_presentation() -> None:
    left = candidate_fact(aliases=("Beta", "Acme"))
    right = candidate_fact(
        source_url="https://example.com:443/public-list",
        aliases=("Acme", "Beta", "Acme"),
        official_website="https://www.acme.ai/",
    )

    assert canonical_candidate_fact(left) == canonical_candidate_fact(right)


@pytest.mark.parametrize(
    ("source_class", "has_website", "expected_tier", "expected_reason"),
    [
        (
            SourceClass.GOVERNMENT,
            True,
            ConfidenceTier.HIGH,
            "government source includes an official website",
        ),
        (
            SourceClass.EXCHANGE,
            False,
            ConfidenceTier.MEDIUM,
            "exchange source does not include an official website",
        ),
        (
            SourceClass.OFFICIAL_COMPANY_SITE,
            False,
            ConfidenceTier.MEDIUM,
            "official_company_site source is an official company site",
        ),
        (
            SourceClass.AUTHORIZED_API,
            True,
            ConfidenceTier.LOW,
            "authorized_api source is an authorized API fallback",
        ),
    ],
)
def test_confidence_is_derived_only_from_registered_source_and_website_evidence(
    source_class: SourceClass,
    has_website: bool,
    expected_tier: ConfidenceTier,
    expected_reason: str,
) -> None:
    fact = candidate_fact(official_website="https://www.acme.ai" if has_website else None)

    assert classify_candidate_confidence(fact, registry_entry(source_class)) == (
        expected_tier,
        expected_reason,
    )


def test_unregistered_source_rolls_back_batch(session: Session, registry: SourceRegistry) -> None:
    with pytest.raises(UnregisteredSourceError):
        import_candidate_facts(
            session,
            [candidate_fact(), candidate_fact(source_id="unknown_source")],
            registry,
        )

    assert session.scalar(select(func.count()).select_from(CandidateFact)) == 0


def test_fallback_only_source_cannot_import_candidate_evidence(session: Session) -> None:
    fallback_registry = SourceRegistry(
        entries=(
            registry_entry().model_copy(
                update={"roles": frozenset({SourceRole.ENTRY_DISCOVERY_FALLBACK})}
            ),
        )
    )

    with pytest.raises(UnregisteredSourceError):
        import_candidate_facts(session, [candidate_fact()], fallback_registry)

    assert session.scalar(select(func.count()).select_from(CandidateFact)) == 0


@pytest.mark.parametrize(
    "outside_url",
    [
        "https://unreviewed.example/public-list",
        "https://example.com/another-list",
    ],
)
def test_source_url_outside_registered_scope_rolls_back_batch(
    session: Session, registry: SourceRegistry, outside_url: str
) -> None:
    with pytest.raises(UnregisteredSourceError):
        import_candidate_facts(
            session,
            [
                candidate_fact(),
                candidate_fact(
                    canonical_name="Outside source",
                    source_url=outside_url,
                ),
            ],
            registry,
        )

    assert session.scalar(select(func.count()).select_from(CandidateFact)) == 0


def test_root_registry_scope_allows_same_origin_evidence_path(session: Session) -> None:
    root_registry = SourceRegistry(
        entries=(registry_entry().model_copy(update={"base_url": "https://example.com/"}),)
    )

    summary = import_candidate_facts(
        session,
        [candidate_fact(source_url="https://example.com/company")],
        root_registry,
    )

    assert summary == CandidateImportSummary(created=1, replayed=0)


def test_non_root_registry_scope_rejects_sibling_prefix(session: Session) -> None:
    list_registry = SourceRegistry(
        entries=(registry_entry().model_copy(update={"base_url": "https://example.com/list"}),)
    )

    with pytest.raises(UnregisteredSourceError):
        import_candidate_facts(
            session,
            [candidate_fact(source_url="https://example.com/listing")],
            list_registry,
        )

    assert session.scalar(select(func.count()).select_from(CandidateFact)) == 0


def test_import_revalidates_bypassed_input_bounds_before_writing(
    session: Session, registry: SourceRegistry
) -> None:
    invalid = candidate_fact().model_construct(canonical_name="x" * 201)

    with pytest.raises(ValidationError, match="at most 200"):
        import_candidate_facts(session, [invalid], registry)

    assert session.scalar(select(func.count()).select_from(CandidateFact)) == 0


def test_import_rejects_unicode_name_that_expands_past_storage_bound(
    session: Session, registry: SourceRegistry
) -> None:
    expanding_name = "\ufb03" * 200

    with pytest.raises(ValueError, match="storage limit"):
        import_candidate_facts(session, [candidate_fact(canonical_name=expanding_name)], registry)

    assert session.scalar(select(func.count()).select_from(CandidateFact)) == 0


def test_conflicting_evidence_identity_rolls_back_batch(
    session: Session, registry: SourceRegistry
) -> None:
    first = candidate_fact()
    conflicting = CandidateFact(
        stable_evidence_id=stable_evidence_id(first),
        canonical_name="Conflicting company",
        normalized_name="conflictingcompany",
        aliases=[],
        primary_category=AiCategory.FOUNDATION_MODELS,
        source_id=first.source_id,
        source_url="https://example.com/public-list",
        retrieved_at=first.retrieved_at,
        evidence_summary=first.evidence_summary,
        confidence_tier=ConfidenceTier.HIGH,
        confidence_reason="government source includes an official website",
        decision_status=CandidateDecisionStatus.REVIEW_REQUIRED,
    )
    session.add(conflicting)
    session.commit()

    with pytest.raises(CandidateEvidenceConflict):
        import_candidate_facts(session, [candidate_fact(canonical_name="Second"), first], registry)

    assert session.scalar(select(func.count()).select_from(CandidateFact)) == 1
