"""Official-website-first enrichment for known companies."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import uuid4

from pydantic import HttpUrl, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.contracts import ProviderQuery, RawDocument
from app.ingestion.errors import ExtractionError, ProviderError
from app.ingestion.extraction.crew import Extractor
from app.ingestion.extraction.schemas import CompanyCandidate, CompanyRef
from app.ingestion.orchestrator import NormalizedBatchBuilder
from app.ingestion.persistence.service import PersistenceError, PersistenceService
from app.ingestion.providers.company_site import CompanySiteProvider
from app.ingestion.providers.http import SafeHttpClient
from app.ingestion.providers.official_news import OfficialNewsProvider
from app.ingestion.providers.robots import RobotsPolicy
from app.ingestion.providers.ymicp import YmicpProvider
from app.models import Company


@dataclass(frozen=True)
class OfficialEnrichmentResult:
    company_id: str
    company_name: str
    status: str
    documents_found: int
    error_code: str | None = None


class OfficialWebsiteEnricher:
    """Refresh one stored company without relying on discovery search."""

    def __init__(self, session: Session, *, extractor: Extractor) -> None:
        self._session = session
        self._extractor = extractor

    async def refresh(self, company: Company) -> OfficialEnrichmentResult:
        if company.website is None:
            return self._result(company, "no_website")
        try:
            host = HttpUrl(company.website).host
        except ValidationError:
            return self._result(company, "invalid_website")
        if host is None:
            return self._result(company, "invalid_website")
        documents, warning = await self._collect(company, host)
        if not documents:
            return self._result(company, warning or "no_official_documents")
        try:
            reference = CompanyRef(name=company.canonical_name, website=company.website)
            profile = await self._extractor.extract_profile(reference, documents)
            discovered = CompanyCandidate(
                name=company.canonical_name,
                website=company.website,
                description=company.description,
                evidence_ids=profile.profile.evidence_ids,
                confidence=profile.profile.confidence,
            )
            outcome = await NormalizedBatchBuilder().build(
                company=reference,
                profile=profile,
                jobs=(),
                documents=documents,
                discovered=discovered,
            )
            if outcome.batch is None:
                return self._result(company, "identity_review_required", len(documents))
            batch = outcome.batch.model_copy(
                update={
                    "company": outcome.batch.company.model_copy(
                        update={"company_id": company.id}
                    )
                }
            )
            PersistenceService(self._session).persist(batch, uuid4())
        except ExtractionError as error:
            return self._result(company, error.code, len(documents))
        except (PersistenceError, ValueError):
            self._session.rollback()
            return self._result(company, "persistence_failed", len(documents))
        except Exception:  # noqa: BLE001 - invalid third-party/model data must not stop the batch.
            self._session.rollback()
            return self._result(company, "invalid_official_data", len(documents))
        return self._result(company, "succeeded", len(documents))

    async def _collect(self, company: Company, host: str) -> tuple[Sequence[RawDocument], str | None]:
        client = SafeHttpClient()
        provider = CompanySiteProvider(
            http_client=client,
            robots_policy=RobotsPolicy(http_client=client),
            approved_hosts=frozenset({host}),
        )
        try:
            result = await provider.search(
                ProviderQuery(
                    query=company.canonical_name,
                    website=company.website,
                    allowed_hosts=frozenset({host}),
                )
            )
        except ProviderError as error:
            return (), error.code

        documents = list(result.documents)
        warnings = list(result.warnings)
        if not documents:
            return (), warnings[0] if warnings else None
        try:
            news_result = await OfficialNewsProvider(
                http_client=client,
                robots_policy=RobotsPolicy(http_client=client),
                approved_hosts=frozenset({host}),
            ).search(
                ProviderQuery(
                    query=company.canonical_name,
                    website=company.website,
                    allowed_hosts=frozenset({host}),
                )
            )
            documents.extend(news_result.documents)
            warnings.extend(news_result.warnings)
        except ProviderError as error:
            warnings.append(error.code)
        try:
            icp_result = await YmicpProvider().search(
                ProviderQuery(query=company.canonical_name, website=company.website)
            )
            documents.extend(icp_result.documents)
            warnings.extend(icp_result.warnings)
        except ProviderError as error:
            warnings.append(error.code)
        return tuple(documents), warnings[0] if warnings else None

    @staticmethod
    def _result(
        company: Company, status: str, documents_found: int = 0
    ) -> OfficialEnrichmentResult:
        return OfficialEnrichmentResult(
            company_id=str(company.id),
            company_name=company.canonical_name,
            status=status,
            documents_found=documents_found,
            error_code=None if status == "succeeded" else status,
        )

    async def refresh_all(
        self,
        *,
        limit: int | None = None,
        on_result: Callable[[tuple[OfficialEnrichmentResult, ...]], None] | None = None,
    ) -> tuple[OfficialEnrichmentResult, ...]:
        statement = select(Company).order_by(Company.canonical_name)
        if limit is not None:
            statement = statement.limit(limit)
        companies = tuple(self._session.scalars(statement))
        for company in companies:
            _ = company.id, company.canonical_name, company.website, company.description
        self._session.expunge_all()
        self._session.rollback()
        results: list[OfficialEnrichmentResult] = []
        for company in companies:
            results.append(await self.refresh(company))
            if on_result is not None:
                on_result(tuple(results))
        return tuple(results)
