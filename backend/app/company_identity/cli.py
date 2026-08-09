"""Pure composition functions for company identity operator commands."""

from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.company_identity.audit import CompanyIdentityAuditService
from app.company_identity.contracts import (
    IdentityAuditReport,
    IdentityReviewApplySummary,
    IdentityReviewDecisionInput,
    IdentityReviewItem,
)
from app.company_identity.repository import CompanyIdentityRepository
from app.company_identity.service import (
    apply_identity_review_decisions,
    export_identity_review_queue,
)


def identity_review_export_payload(session: Session) -> tuple[IdentityReviewItem, ...]:
    return export_identity_review_queue(session)


def identity_review_apply_payload(
    session: Session,
    decisions: Sequence[IdentityReviewDecisionInput],
) -> IdentityReviewApplySummary:
    return apply_identity_review_decisions(session, decisions)


def company_identity_audit_payload(
    session: Session,
    repository: CompanyIdentityRepository,
) -> IdentityAuditReport:
    return CompanyIdentityAuditService(session, repository).build()
