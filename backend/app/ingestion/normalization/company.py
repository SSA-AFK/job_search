"""Company candidate normalization."""

from dataclasses import dataclass

from app.core.normalization import normalize_name
from app.ingestion.extraction.schemas import CompanyCandidate


@dataclass(frozen=True)
class NormalizedCompanyCandidate:
    candidate: CompanyCandidate
    normalized_name: str


def normalize_company(candidate: CompanyCandidate) -> NormalizedCompanyCandidate:
    return NormalizedCompanyCandidate(candidate=candidate, normalized_name=normalize_name(candidate.name))
