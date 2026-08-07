from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.company_identity.contracts import (
    CompanyIdentityCandidateMatch,
    CompanyIdentityInput,
    CompanyIdentityResolution,
    CompanyIdentityReviewDraft,
    IdentityAuditFinding,
    IdentityAuditReport,
    IdentityAuditSeverity,
    IdentityResolutionKind,
    IdentityReviewAction,
    IdentityReviewDecisionInput,
    IdentityReviewReason,
    IdentityReviewStatus,
    PublicEvidenceReference,
)

COMPANY_ID = UUID("00000000-0000-0000-0000-000000000001")
REVIEW_ID = UUID("00000000-0000-0000-0000-000000000002")


def utc(day: int) -> datetime:
    return datetime(2026, 8, day, 12, 30, tzinfo=UTC)


def identity(*, evidence_confidence: Decimal = Decimal("0.90")) -> CompanyIdentityInput:
    return CompanyIdentityInput(
        canonical_name="  OPEN\u00a0AI  ",
        aliases=("OpenAI CN", "OPENAI\u00a0CN"),
        official_website="HTTPS://OpenAI.COM/?campaign=test#about",
        recruitment_identity="  TENANT:jobs.example.com:OpenAI  ",
        legal_identifiers=(" CN-123 ", "cn-123"),
        city="  Shanghai  ",
        evidence=(
            PublicEvidenceReference(
                provider="official_site",
                url="https://OpenAI.com/about?utm_source=test#team",
                evidence_id="document-1",
                confidence=evidence_confidence,
            ),
        ),
    )


def review_draft(*, match_score: Decimal, observed_at: datetime) -> CompanyIdentityReviewDraft:
    return CompanyIdentityReviewDraft(
        identity=identity(),
        candidate_matches=(
            CompanyIdentityCandidateMatch(
                company_id=COMPANY_ID,
                canonical_name="OpenAI",
                normalized_name="openai",
                match_kind="fuzzy_name",
                score=match_score,
                conflict_reasons=(IdentityReviewReason.FUZZY_NAME_NEIGHBOR,),
            ),
        ),
        review_reasons=(IdentityReviewReason.FUZZY_NAME_NEIGHBOR,),
        observed_at=observed_at,
    )


def candidate_match(
    *,
    company_id: UUID = COMPANY_ID,
    canonical_name: str = "OpenAI",
    score: Decimal = Decimal(90),
) -> CompanyIdentityCandidateMatch:
    return CompanyIdentityCandidateMatch(
        company_id=company_id,
        canonical_name=canonical_name,
        normalized_name="openai" if canonical_name == "OpenAI" else canonical_name.lower(),
        match_kind="fuzzy_name",
        score=score,
    )


def test_identity_input_is_frozen_bounded_and_rejects_extra_fields() -> None:
    value = identity()

    with pytest.raises(ValidationError):
        value.canonical_name = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        CompanyIdentityInput.model_validate({**value.model_dump(), "unknown": "field"})
    with pytest.raises(ValidationError):
        CompanyIdentityInput(
            canonical_name="x" * 201,
            aliases=(),
            official_website=None,
            recruitment_identity=None,
            legal_identifiers=(),
            city=None,
            evidence=(),
        )
    with pytest.raises(ValidationError):
        CompanyIdentityInput(
            canonical_name="OpenAI",
            aliases=tuple(f"alias-{index}" for index in range(101)),
            official_website=None,
            recruitment_identity=None,
            legal_identifiers=(),
            city=None,
            evidence=(),
        )


def test_identity_input_rejects_whitespace_only_canonical_name() -> None:
    with pytest.raises(ValidationError):
        CompanyIdentityInput(
            canonical_name="   ",
            aliases=(),
            official_website=None,
            recruitment_identity=None,
            legal_identifiers=(),
            city=None,
            evidence=(),
        )


def test_identity_input_normalizes_names_and_deduplicates_aliases() -> None:
    value = identity()

    assert value.normalized_name == "openai"
    assert value.normalized_aliases == ("openaicn",)
    assert value.aliases == ("OpenAI CN",)
    assert value.legal_identifiers == ("cn-123",)
    assert value.city == "shanghai"
    assert value.recruitment_identity == "tenant:jobs.example.com:openai"
    assert value.official_website == "https://openai.com/"


def test_public_evidence_removes_query_and_fragment_and_rejects_userinfo() -> None:
    value = identity().evidence[0]

    assert value.url == "https://openai.com/about"
    with pytest.raises(ValidationError, match="credentials"):
        PublicEvidenceReference(
            provider="official_site",
            url="https://user:password@example.com/about",
            evidence_id="document-1",
            confidence=Decimal("0.90"),
        )

    with pytest.raises(ValidationError):
        PublicEvidenceReference(
            provider="official_site",
            url="https://example.com/about",
            evidence_id="   ",
            confidence=Decimal("0.90"),
        )


def test_review_draft_hash_uses_normalized_public_identity_not_score_or_time() -> None:
    left = review_draft(match_score=Decimal("91.0"), observed_at=utc(7))
    right = review_draft(match_score=Decimal("88.0"), observed_at=utc(8))

    assert left.stable_identity_hash == right.stable_identity_hash
    assert left.stable_identity_hash == "6f37151e67ff47be3e8d88853cd1f7a25e650cc9ec0f9c47d4966b700ae5464a"
    assert len(left.stable_identity_hash) == 64
    assert left.evidence[0].url == "https://openai.com/about"


def test_review_draft_hash_normalizes_equivalent_decimal_confidence() -> None:
    left = CompanyIdentityReviewDraft(
        identity=identity(evidence_confidence=Decimal("0.9")),
        candidate_matches=(),
        review_reasons=(IdentityReviewReason.FUZZY_NAME_NEIGHBOR,),
        observed_at=utc(7),
    )
    right = CompanyIdentityReviewDraft(
        identity=identity(evidence_confidence=Decimal("0.90")),
        candidate_matches=(),
        review_reasons=(IdentityReviewReason.FUZZY_NAME_NEIGHBOR,),
        observed_at=utc(8),
    )

    assert left.stable_identity_hash == right.stable_identity_hash


def test_review_draft_hash_normalizes_signed_zero_confidence() -> None:
    left = CompanyIdentityReviewDraft(
        identity=identity(evidence_confidence=Decimal("-0.0")),
        candidate_matches=(),
        review_reasons=(IdentityReviewReason.FUZZY_NAME_NEIGHBOR,),
        observed_at=utc(7),
    )
    right = CompanyIdentityReviewDraft(
        identity=identity(evidence_confidence=Decimal(0)),
        candidate_matches=(),
        review_reasons=(IdentityReviewReason.FUZZY_NAME_NEIGHBOR,),
        observed_at=utc(8),
    )

    assert left.stable_identity_hash == right.stable_identity_hash


def test_resolution_canonicalizes_candidate_order_and_review_reason_set() -> None:
    result = CompanyIdentityResolution(
        kind=IdentityResolutionKind.REVIEW_REQUIRED,
        company_id=None,
        stable_identity_hash="a" * 64,
        candidate_matches=(
            CompanyIdentityCandidateMatch(
                company_id=UUID("00000000-0000-0000-0000-000000000003"),
                canonical_name="Later Match",
                normalized_name="latermatch",
                match_kind="fuzzy_name",
                score=Decimal(80),
            ),
            CompanyIdentityCandidateMatch(
                company_id=COMPANY_ID,
                canonical_name="Earlier Match",
                normalized_name="earliermatch",
                match_kind="fuzzy_name",
                score=Decimal(90),
            ),
        ),
        review_reasons=(
            IdentityReviewReason.FUZZY_NAME_NEIGHBOR,
            IdentityReviewReason.AMBIGUOUS_EXACT_OWNER,
            IdentityReviewReason.FUZZY_NAME_NEIGHBOR,
        ),
    )

    assert [match.company_id for match in result.candidate_matches] == [
        COMPANY_ID,
        UUID("00000000-0000-0000-0000-000000000003"),
    ]
    assert result.review_reasons == (
        IdentityReviewReason.AMBIGUOUS_EXACT_OWNER,
        IdentityReviewReason.FUZZY_NAME_NEIGHBOR,
    )


def test_resolution_rejects_collections_that_conflict_with_its_kind() -> None:
    with pytest.raises(ValidationError):
        CompanyIdentityResolution(
            kind=IdentityResolutionKind.EXISTING,
            company_id=COMPANY_ID,
            stable_identity_hash="a" * 64,
            review_reasons=(IdentityReviewReason.FUZZY_NAME_NEIGHBOR,),
        )
    with pytest.raises(ValidationError):
        CompanyIdentityResolution(
            kind=IdentityResolutionKind.NEW,
            stable_identity_hash="a" * 64,
            candidate_matches=(candidate_match(),),
        )
    with pytest.raises(ValidationError):
        CompanyIdentityResolution(
            kind=IdentityResolutionKind.REVIEW_REQUIRED,
            stable_identity_hash="a" * 64,
        )


def test_candidate_match_requires_the_normalized_canonical_name_without_echoing_input() -> None:
    hostile_name = "do-not-echo-this-name"

    with pytest.raises(ValidationError) as error:
        CompanyIdentityCandidateMatch(
            company_id=COMPANY_ID,
            canonical_name=hostile_name,
            normalized_name="different",
            match_kind="fuzzy_name",
            score=Decimal(90),
        )

    assert hostile_name not in str(error.value)
    assert "normalized_name is invalid" in str(error.value)


def test_resolution_orders_tied_candidates_by_name_then_uuid() -> None:
    first_company_id = UUID("00000000-0000-0000-0000-000000000001")
    second_company_id = UUID("00000000-0000-0000-0000-000000000002")
    result = CompanyIdentityResolution(
        kind=IdentityResolutionKind.REVIEW_REQUIRED,
        stable_identity_hash="a" * 64,
        candidate_matches=(
            candidate_match(company_id=second_company_id, canonical_name="OpenAI", score=Decimal(88)),
            candidate_match(company_id=first_company_id, canonical_name="OpenAI", score=Decimal(88)),
        ),
        review_reasons=(IdentityReviewReason.FUZZY_NAME_NEIGHBOR,),
    )

    assert [match.company_id for match in result.candidate_matches] == [
        first_company_id,
        second_company_id,
    ]


def test_resolution_and_review_draft_reject_duplicate_company_candidates() -> None:
    duplicate_candidates = (
        candidate_match(score=Decimal(91)),
        candidate_match(score=Decimal(88)),
    )

    with pytest.raises(ValidationError):
        CompanyIdentityResolution(
            kind=IdentityResolutionKind.REVIEW_REQUIRED,
            stable_identity_hash="a" * 64,
            candidate_matches=duplicate_candidates,
            review_reasons=(IdentityReviewReason.FUZZY_NAME_NEIGHBOR,),
        )
    with pytest.raises(ValidationError):
        CompanyIdentityReviewDraft(
            identity=identity(),
            candidate_matches=duplicate_candidates,
            review_reasons=(IdentityReviewReason.FUZZY_NAME_NEIGHBOR,),
            observed_at=utc(7),
        )


def test_candidate_match_rejects_whitespace_only_canonical_name() -> None:
    with pytest.raises(ValidationError):
        CompanyIdentityCandidateMatch(
            company_id=COMPANY_ID,
            canonical_name="   ",
            normalized_name="openai",
            match_kind="fuzzy_name",
            score=Decimal(91),
        )


def test_enums_expose_only_the_stable_contract_values() -> None:
    assert {member.value for member in IdentityResolutionKind} == {
        "existing",
        "new",
        "review_required",
    }
    assert {member.value for member in IdentityReviewStatus} == {
        "pending",
        "resolved",
        "rejected",
    }
    assert {member.value for member in IdentityReviewAction} == {
        "link_as_alias",
        "create_new",
        "rename_canonical",
        "reject",
    }
    assert {member.value for member in IdentityReviewReason} == {
        "ambiguous_exact_owner",
        "fuzzy_name_neighbor",
        "short_name_collision",
        "website_identity_conflict",
        "recruitment_identity_conflict",
        "legal_identity_conflict",
        "similarity_search_unavailable",
    }
    assert {member.value for member in IdentityAuditSeverity} == {
        "critical",
        "important",
        "minor",
    }


def test_datetimes_are_utc_and_serialize_with_z() -> None:
    decision = IdentityReviewDecisionInput(
        review_item_id=REVIEW_ID,
        action=IdentityReviewAction.REJECT,
        target_company_id=None,
        reason="Insufficient public evidence.",
        decided_at=utc(7).astimezone(timezone(-timedelta(hours=8))),
    )

    assert decision.decided_at == utc(7)
    assert decision.model_dump(mode="json")["decided_at"] == "2026-08-07T12:30:00Z"


def test_review_reason_rejection_uses_a_fixed_message_without_echoing_input() -> None:
    hostile_reason = "token=do-not-echo-this-value"

    with pytest.raises(ValidationError) as error:
        IdentityReviewDecisionInput(
            review_item_id=REVIEW_ID,
            action=IdentityReviewAction.REJECT,
            target_company_id=None,
            reason=hostile_reason,
            decided_at=utc(7),
        )

    assert hostile_reason not in str(error.value)
    assert "reason is invalid" in str(error.value)


def test_create_new_rejects_unused_target_company_id() -> None:
    with pytest.raises(ValidationError):
        IdentityReviewDecisionInput(
            review_item_id=REVIEW_ID,
            action=IdentityReviewAction.CREATE_NEW,
            target_company_id=COMPANY_ID,
            reason="Create a distinct company.",
            decided_at=utc(7),
        )


def test_review_draft_rejects_more_than_bounded_candidate_matches() -> None:
    match = review_draft(match_score=Decimal("91.0"), observed_at=utc(7)).candidate_matches[0]

    with pytest.raises(ValidationError):
        CompanyIdentityReviewDraft(
            identity=identity(),
            candidate_matches=(match,) * 21,
            review_reasons=(IdentityReviewReason.FUZZY_NAME_NEIGHBOR,),
            observed_at=utc(7),
        )


def test_audit_counts_are_immutable_return_values() -> None:
    report = IdentityAuditReport(
        findings=(),
        scanned_companies=3,
        scanned_aliases=2,
        scanned_review_items=1,
        finding_counts={IdentityAuditSeverity.CRITICAL: 0},
    )

    assert report.finding_counts == {IdentityAuditSeverity.CRITICAL: 0}
    assert isinstance(report.finding_counts, MappingProxyType)
    with pytest.raises(TypeError):
        report.finding_counts[IdentityAuditSeverity.MINOR] = 1  # type: ignore[index]


def test_audit_finding_canonicalizes_safe_display_names_and_evidence_codes() -> None:
    finding = IdentityAuditFinding(
        finding_id="a" * 64,
        code="cross_table_name_owner",
        severity=IdentityAuditSeverity.CRITICAL,
        company_ids=(COMPANY_ID,),
        display_names=("  OpenAI  ", "OpenAI"),
        evidence_codes=(" Name_Owner ", "name_owner"),
        recommended_action="review",
    )

    assert finding.display_names == ("OpenAI",)
    assert finding.evidence_codes == ("name_owner",)


def test_audit_finding_rejects_hostile_or_oversize_public_values_without_echoing_input() -> None:
    hostile_code = "token=do-not-echo"

    with pytest.raises(ValidationError) as error:
        IdentityAuditFinding(
            finding_id="a" * 64,
            code="cross_table_name_owner",
            severity=IdentityAuditSeverity.CRITICAL,
            company_ids=(COMPANY_ID,),
            display_names=("x" * 201,),
            evidence_codes=(hostile_code,),
            recommended_action="review",
        )

    assert hostile_code not in str(error.value)
    assert "display_names is invalid" in str(error.value)
    assert "evidence_codes is invalid" in str(error.value)


@pytest.mark.parametrize("hostile_name", ("<script>", "Acme\u0007"))
def test_audit_finding_rejects_unsafe_display_names_without_echoing_input(
    hostile_name: str,
) -> None:

    with pytest.raises(ValidationError) as error:
        IdentityAuditFinding(
            finding_id="a" * 64,
            code="cross_table_name_owner",
            severity=IdentityAuditSeverity.CRITICAL,
            company_ids=(COMPANY_ID,),
            display_names=(hostile_name,),
            recommended_action="review",
        )

    assert hostile_name not in str(error.value)
    assert "display_names is invalid" in str(error.value)


def test_audit_count_mapping_and_json_follow_severity_declaration_order() -> None:
    report = IdentityAuditReport(
        findings=(),
        scanned_companies=3,
        scanned_aliases=2,
        scanned_review_items=1,
        finding_counts={
            IdentityAuditSeverity.MINOR: 3,
            IdentityAuditSeverity.IMPORTANT: 2,
            IdentityAuditSeverity.CRITICAL: 1,
        },
    )

    assert tuple(report.finding_counts) == (
        IdentityAuditSeverity.CRITICAL,
        IdentityAuditSeverity.IMPORTANT,
        IdentityAuditSeverity.MINOR,
    )
    assert list(report.model_dump(mode="json")["finding_counts"]) == [
        "critical",
        "important",
        "minor",
    ]
