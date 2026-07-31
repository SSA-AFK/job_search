from enum import StrEnum


class CollectionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class RunType(StrEnum):
    DISCOVERY = "discovery"
    COMPANY_REFRESH = "company_refresh"
    ON_DEMAND = "on_demand"
    EXPIRATION = "expiration"


class JobType(StrEnum):
    FULL_TIME = "full_time"
    INTERNSHIP = "internship"
    CAMPUS = "campus"
    EXPERIENCED = "experienced"
    UNKNOWN = "unknown"


class FilingType(StrEnum):
    ICP = "icp"
    ALGORITHM = "algorithm"
    BUSINESS_LICENSE = "business_license"
