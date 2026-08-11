from decimal import Decimal

from app.manifest.contracts import AtsClassification, CandidateDecisionStatus, SourceRole
from app.manifest.evidence import ModelEvidenceAssessment, ModelRiskLabel, PublicEvidence
from app.manifest.evidence_policy import (
    AuditFinding,
    AuditSampleSelector,
    AuditStratum,
    EntryEvidenceCandidate,
    EvidenceAcceptancePolicy,
    IndependentEvidenceValidation,
    OwnershipValidation,
    RegisteredSourceValidation,
    RobotsValidation,
)


def validation(
    *, source_id: str = "zjsia", platform: str = "moka"
) -> IndependentEvidenceValidation:
    source_url = "https://association.example.org/members/acme"
    candidate_url = "https://www.acme.example/careers"
    return IndependentEvidenceValidation(
        registered_source=RegisteredSourceValidation(
            source_id=source_id,
            exact_source_url=source_url,
            role=SourceRole.CANDIDATE_POOL,
            registry_fingerprint="f" * 64,
        ),
        robots=RobotsValidation(exact_candidate_url=candidate_url, allowed=True),
        ownership=OwnershipValidation(
            exact_source_url=source_url,
            exact_candidate_url=candidate_url,
            basis="official_navigation_anchor",
            detail="Careers",
        ),
        classification=AtsClassification(platform=platform),
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
        "independent_validation": validation(),
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
    incomplete = validation().model_copy(update={"registered_source": None})
    decision = EvidenceAcceptancePolicy().evaluate(
        candidate(independent_validation=incomplete), assessment()
    )

    assert decision.status is CandidateDecisionStatus.REJECTED
    assert decision.reason_code == "unregistered_source"


def test_hard_rules_and_high_model_confidence_are_accepted() -> None:
    decision = EvidenceAcceptancePolicy().evaluate(candidate(), assessment())

    assert decision.status is CandidateDecisionStatus.ACCEPTED
    assert decision.reason_code == "accepted"


def test_boolean_and_text_assertions_cannot_auto_accept_without_independent_validation() -> None:
    decision = EvidenceAcceptancePolicy().evaluate(
        candidate(independent_validation=None), assessment()
    )

    assert decision.status is CandidateDecisionStatus.REVIEW_REQUIRED
    assert decision.reason_code == "independent_validation_required"


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
    other = policy.evaluate(
        candidate(platform="feishu", independent_validation=validation(platform="feishu")),
        assessment(),
    )

    assert paused.status is CandidateDecisionStatus.REVIEW_REQUIRED
    assert paused.reason_code == "audit_stratum_paused"
    assert other.status is CandidateDecisionStatus.ACCEPTED
