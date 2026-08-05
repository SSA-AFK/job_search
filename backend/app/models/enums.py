from enum import StrEnum


class FundingStage(StrEnum):
    SEED = "seed"
    ANGEL = "angel"
    PRE_A = "pre_a"
    SERIES_A = "series_a"
    SERIES_B = "series_b"
    SERIES_C_PLUS = "series_c_plus"
    PUBLIC = "public"
    UNFUNDED = "unfunded"
    UNKNOWN = "unknown"


class CompanyScale(StrEnum):
    ONE_TO_49 = "one_to_49"
    FIFTY_TO_199 = "50_to_199"
    TWO_HUNDRED_TO_499 = "200_to_499"
    FIVE_HUNDRED_PLUS = "500_plus"
    UNKNOWN = "unknown"


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
    PART_TIME = "part_time"
    INTERNSHIP = "internship"
    TEMPORARY = "temporary"
    CAMPUS = "campus"
    EXPERIENCED = "experienced"
    UNKNOWN = "unknown"


class JobEntryStatus(StrEnum):
    UNKNOWN = "unknown"
    ACTIVE = "active"
    STALE = "stale"
    DISABLED = "disabled"


class JobSnapshotStatus(StrEnum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class FilingType(StrEnum):
    ICP = "icp"
    ALGORITHM = "algorithm"
    BUSINESS_LICENSE = "business_license"
