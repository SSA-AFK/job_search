"""Funding-event verification rules."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import FundingEvent, FundingEventSource, VerificationStatus

_MINIMUM_INDEPENDENT_SOURCES = 2


def refresh_funding_verification(session: Session, event: FundingEvent) -> VerificationStatus:
    source_count = session.scalar(
        select(func.count(FundingEventSource.source_document_id)).where(
            FundingEventSource.funding_event_id == event.id
        )
    ) or 0
    event.verification_status = (
        VerificationStatus.VERIFIED
        if source_count >= _MINIMUM_INDEPENDENT_SOURCES
        else VerificationStatus.PENDING_VERIFICATION
    )
    return event.verification_status
