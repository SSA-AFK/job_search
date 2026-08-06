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
)
from app.models.filing import RegulatoryFiling
from app.models.job import JobPosting, JobSource
from app.models.job_entry import JobCollectionSnapshot, JobEntry
from app.models.source import CompanySource, SourceDocument

if TYPE_CHECKING:
    from app.manifest.models import (
        CandidateFact,
        CandidateReview,
        CompanyManifest,
        CompanyManifestMember,
        EntryDiscoveryObservation,
    )


_MANIFEST_MODEL_NAMES = frozenset(
    {
        "CandidateFact",
        "CandidateReview",
        "CompanyManifest",
        "CompanyManifestMember",
        "EntryDiscoveryObservation",
    }
)


def _load_manifest_models() -> None:
    from app.manifest import models

    _ = models


def __getattr__(name: str) -> object:
    if name == "Base":
        _load_manifest_models()
        return _Base
    if name in _MANIFEST_MODEL_NAMES:
        _load_manifest_models()
        from app.manifest import models

        return getattr(models, name)
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
    "CompanyManifest",
    "CompanyManifestMember",
    "CompanyScale",
    "CompanySource",
    "CrawlRun",
    "EntryDiscoveryObservation",
    "FilingType",
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
]
