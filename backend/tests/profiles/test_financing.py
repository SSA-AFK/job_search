from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import (
    Base,
    Company,
    FundingEvent,
    FundingEventSource,
    SourceDocument,
    VerificationStatus,
)
from app.profiles.financing import refresh_funding_verification


def test_funding_event_requires_two_distinct_sources_for_verification() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    collected_at = datetime(2026, 8, 10, tzinfo=UTC)
    with Session(engine) as session:
        company = Company(canonical_name="Example", normalized_name="example")
        session.add(company)
        session.flush()
        event = FundingEvent(
            company_id=company.id,
            round_label="series_a",
            collected_at=collected_at,
        )
        session.add(event)
        session.flush()
        sources = [
            SourceDocument(
                provider=f"source_{index}",
                external_id="event-1",
                url=f"https://example{index}.com/funding",
                title=None,
                text_excerpt="Funding announcement",
                content_hash=str(index) * 64,
                authority_level=2,
                fetched_at=collected_at,
            )
            for index in (1, 2)
        ]
        session.add_all(sources)
        session.flush()

        session.add(FundingEventSource(funding_event_id=event.id, source_document_id=sources[0].id))
        assert refresh_funding_verification(session, event) is VerificationStatus.PENDING_VERIFICATION

        session.add(FundingEventSource(funding_event_id=event.id, source_document_id=sources[1].id))
        assert refresh_funding_verification(session, event) is VerificationStatus.VERIFIED
