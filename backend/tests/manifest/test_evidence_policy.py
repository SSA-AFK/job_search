from decimal import Decimal

from app.manifest.contracts import CandidateDecisionStatus
from app.manifest.evidence import ModelEvidenceAssessment, ModelRiskLabel, PublicEvidence
from app.manifest.evidence_policy import (
    AuditFinding,
    AuditSampleSelector,
    AuditStratum,
    EntryEvidenceCandidate,
    EvidenceAcceptancePolicy,
)


def candidate(**overrides: object) -> EntryEvidenceCandidate:
    values: dict[str, object] = {
        "source_id": "zjsia",
        "platform": "moka",
        "evidence": PublicEvidence(
            source_url="https://association.example.org/members/acme",
            candidate_url="https://www.acme.example/careers",
            page_title="Acme AI | Careers",
            visible_summary="Acme AI builds foundation models.",
            anchor_text="Join Acme",
        ),
        "source_registered": True,
        "robots_approved": True,
        "ownership_evidence": "official_navigation_anchor:Careers",
    }
    values.update(overrides)
    return EntryEvidenceCandidate(**values)


def assessment(**overrides: object) -> ModelEvidenceAssessment:
    values: dict[str, object] = {
        "confidence": Decimal("0.95"),
        "rationale": "The official site links to this recruitment entry.",
        "risk_labels": frozenset(),
    }
    values.update(overrides)
    return ModelEvidenceAssessment(**values)


def test_hard_rule_failure_is_rejected_before_model_confidence() -> None:
    decision = EvidenceAcceptancePolicy().evaluate(candidate(robots_approved=False), assessment())

    assert decision.status is CandidateDecisionStatus.REJECTED
    assert decision.reason_code == "robots_disallowed"


def test_hard_rules_and_high_model_confidence_are_accepted() -> None:
    decision = EvidenceAcceptancePolicy().evaluate(candidate(), assessment())

    assert decision.status is CandidateDecisionStatus.ACCEPTED
    assert decision.reason_code == "accepted"


def test_low_confidence_or_model_risk_routes_to_review() -> None:
    low_confidence = EvidenceAcceptancePolicy().evaluate(
        candidate(), assessment(confidence=Decimal("0.89"))
    )
    risky = EvidenceAcceptancePolicy().evaluate(
        candidate(), assessment(risk_labels=frozenset({ModelRiskLabel.CROSS_HOST}))
    )

    assert low_confidence.status is CandidateDecisionStatus.REVIEW_REQUIRED
    assert low_confidence.reason_code == "model_confidence_below_threshold"
    assert risky.status is CandidateDecisionStatus.REVIEW_REQUIRED
    assert risky.reason_code == "model_risk_flagged"


def test_five_percent_audit_selection_is_deterministic_per_source_and_platform() -> None:
    candidates = tuple(
        candidate(
            source_id="zjsia" if index < 20 else "szaia",
            platform="moka" if index % 2 == 0 else "feishu",
            evidence=PublicEvidence(
                source_url="https://association.example.org/members/acme",
                candidate_url=f"https://www.acme.example/careers/{index}",
                page_title="Acme AI | Careers",
                visible_summary="Acme AI builds foundation models.",
                anchor_text="Join Acme",
            ),
        )
        for index in range(40)
    )
    selector = AuditSampleSelector()

    first = selector.select(candidates)
    second = selector.select(reversed(candidates))

    assert first == second
    assert len(first) == 4
    assert {item.stratum for item in first} == {
        AuditStratum(source_id="zjsia", platform="moka"),
        AuditStratum(source_id="zjsia", platform="feishu"),
        AuditStratum(source_id="szaia", platform="moka"),
        AuditStratum(source_id="szaia", platform="feishu"),
    }


def test_severe_audit_error_pauses_only_its_source_platform_stratum() -> None:
    policy = EvidenceAcceptancePolicy(
        paused_strata=AuditSampleSelector.paused_strata(
            (
                AuditFinding(
                    stratum=AuditStratum(source_id="zjsia", platform="moka"),
                    severe_error=True,
                ),
                AuditFinding(
                    stratum=AuditStratum(source_id="zjsia", platform="feishu"),
                    severe_error=False,
                ),
            )
        )
    )

    paused = policy.evaluate(candidate(), assessment())
    other = policy.evaluate(candidate(platform="feishu"), assessment())

    assert paused.status is CandidateDecisionStatus.REVIEW_REQUIRED
    assert paused.reason_code == "audit_stratum_paused"
    assert other.status is CandidateDecisionStatus.ACCEPTED
