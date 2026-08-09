from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.manifest.evidence import (
    DashScopeEvidenceClient,
    ModelDisabledError,
    ModelEvidenceAssessment,
    PublicEvidence,
)


def evidence(**overrides: object) -> PublicEvidence:
    values: dict[str, object] = {
        "source_url": "https://association.example.org/members/acme",
        "candidate_url": "https://www.acme.example/careers",
        "page_title": "Acme AI | Careers",
        "visible_summary": "Acme AI builds foundation models.",
        "anchor_text": "Join Acme",
    }
    values.update(overrides)
    return PublicEvidence(**values)


def test_public_evidence_projects_only_model_safe_fields() -> None:
    payload = evidence().model_payload()

    assert payload.model_dump(mode="json") == {
        "source_url": "https://association.example.org/members/acme",
        "candidate_url": "https://www.acme.example/careers",
        "page_title": "Acme AI | Careers",
        "visible_summary": "Acme AI builds foundation models.",
        "anchor_text": "Join Acme",
    }


def test_public_evidence_rejects_credentials_and_sensitive_text() -> None:
    with pytest.raises(ValidationError, match="without credentials"):
        evidence(candidate_url="https://token@www.acme.example/careers")
    with pytest.raises(ValidationError, match="sensitive value"):
        evidence(visible_summary="DATABASE_URL=postgresql://user:password@db.example/app")


def test_model_assessment_rejects_unknown_and_invalid_structured_output() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ModelEvidenceAssessment(
            confidence="0.95",
            rationale="The official site links to this recruitment entry.",
            risk_labels=[],
            raw_response="must not be retained",
        )
    with pytest.raises(ValidationError, match="less than or equal to 1"):
        ModelEvidenceAssessment(
            confidence="1.01",
            rationale="The official site links to this recruitment entry.",
            risk_labels=[],
        )


def test_model_client_is_disabled_by_default_without_requesting_a_model() -> None:
    client = DashScopeEvidenceClient()

    with pytest.raises(ModelDisabledError, match="disabled"):
        client.classify(evidence())


@pytest.mark.parametrize("value", ["-0.01", "1.01"])
def test_confidence_threshold_must_be_a_probability(value: str) -> None:
    with pytest.raises(ValidationError):
        DashScopeEvidenceClient(confidence_threshold=Decimal(value))


def test_settings_keep_model_classification_disabled_by_default() -> None:
    settings = Settings(entry_evidence_model_confidence_threshold="0.91")

    assert settings.entry_evidence_model_enabled is False
    assert settings.entry_evidence_model_name == "qwen-plus"
    assert settings.entry_evidence_model_confidence_threshold == Decimal("0.91")
