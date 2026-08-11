from typing import TYPE_CHECKING

from app.models.base import GUID, TimestampMixin, UTCDateTime
from app.models.base import Base as _Base
from app.models.collection import CollectionRequest, CrawlRun
from app.models.company import Company, CompanyAlias
from app.models.enums import (
    CollectionStatus,
    CompanyScale,
    FilingType,
    FundingStage,
    JobEntryStatus,
    JobSnapshotStatus,
    JobType,
    RunType,
    VerificationStatus,
)
from app.models.filing import RegulatoryFiling
from app.models.financing import FundingEvent, FundingEventSource, FundingInvestor
from app.models.job import JobPosting, JobSource
from app.models.job_entry import JobCollectionSnapshot, JobEntry
from app.models.source import CompanyProfileField, CompanySource, SourceDocument

if TYPE_CHECKING:
    from app.company_identity.models import (
        CompanyIdentityReviewDecision,
        CompanyIdentityReviewItem,
    )
    from app.manifest.models import (
        CandidateFact,
        CandidateReview,
        CompanyManifest,
        CompanyManifestMember,
        EntryDiscoveryObservation,
        EntryDiscoveryRound,
        EntryEvidenceAuditFinding,
        EntryEvidenceAuditSample,
    )


_MANIFEST_MODEL_NAMES = frozenset(
    {
        "CandidateFact",
        "CandidateReview",
        "CompanyManifest",
        "CompanyManifestMember",
        "EntryDiscoveryObservation",
        "EntryDiscoveryRound",
        "EntryEvidenceAuditFinding",
        "EntryEvidenceAuditSample",
    }
)

_COMPANY_IDENTITY_MODEL_NAMES = frozenset(
    {
        "CompanyIdentityReviewDecision",
        "CompanyIdentityReviewItem",
    }
)


def _load_manifest_models() -> None:
    from app.manifest import models

    _ = models


def _load_company_identity_models() -> None:
    from app.company_identity import models

    _ = models


def __getattr__(name: str) -> object:
    if name == "Base":
        _load_manifest_models()
        _load_company_identity_models()
        return _Base
    if name in _MANIFEST_MODEL_NAMES:
        _load_manifest_models()
        from app.manifest import models as manifest_models

        return getattr(manifest_models, name)
    if name in _COMPANY_IDENTITY_MODEL_NAMES:
        _load_company_identity_models()
        from app.company_identity import models as company_identity_models

        return getattr(company_identity_models, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "GUID",
    "Base",
    "CandidateFact",
    "CandidateReview",
    "CollectionRequest",
    "CollectionStatus",
    "Company",
    "CompanyAlias",
    "CompanyIdentityReviewDecision",
    "CompanyIdentityReviewItem",
    "CompanyManifest",
    "CompanyManifestMember",
    "CompanyProfileField",
    "CompanyScale",
    "CompanySource",
    "CrawlRun",
    "EntryDiscoveryObservation",
    "EntryDiscoveryRound",
    "EntryEvidenceAuditFinding",
    "EntryEvidenceAuditSample",
    "FilingType",
    "FundingEvent",
    "FundingEventSource",
    "FundingInvestor",
    "FundingStage",
    "JobCollectionSnapshot",
    "JobEntry",
    "JobEntryStatus",
    "JobPosting",
    "JobSnapshotStatus",
    "JobSource",
    "JobType",
    "RegulatoryFiling",
    "RunType",
    "SourceDocument",
    "TimestampMixin",
    "UTCDateTime",
    "VerificationStatus",
]
