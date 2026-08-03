"""Job candidate normalization."""

from dataclasses import dataclass

from app.core.normalization import normalize_name
from app.ingestion.extraction.schemas import EmploymentType, JobCandidate
from app.ingestion.normalization.salary import normalize_salary
from app.models.enums import JobType

_JOB_TYPE_BY_EMPLOYMENT_TYPE = {
    EmploymentType.FULL_TIME: JobType.FULL_TIME,
    EmploymentType.INTERNSHIP: JobType.INTERNSHIP,
    EmploymentType.PART_TIME: JobType.UNKNOWN,
    EmploymentType.TEMPORARY: JobType.UNKNOWN,
}


@dataclass(frozen=True)
class NormalizedJobCandidate:
    candidate: JobCandidate
    normalized_title: str
    normalized_city: str
    job_type: JobType
    salary_minimum_monthly: int | None
    salary_maximum_monthly: int | None
    salary_months: int | None
    warnings: tuple[str, ...]


def normalize_job(candidate: JobCandidate) -> NormalizedJobCandidate:
    salary = normalize_salary(candidate.salary)
    job_type = (
        _JOB_TYPE_BY_EMPLOYMENT_TYPE[candidate.employment_type]
        if candidate.employment_type is not None
        else JobType.UNKNOWN
    )
    return NormalizedJobCandidate(
        candidate=candidate,
        normalized_title=normalize_name(candidate.title),
        normalized_city=normalize_name(candidate.location) if candidate.location else "",
        job_type=job_type,
        salary_minimum_monthly=salary.minimum_monthly,
        salary_maximum_monthly=salary.maximum_monthly,
        salary_months=salary.months,
        warnings=salary.warnings,
    )
