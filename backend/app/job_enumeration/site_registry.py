import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.normalization import normalize_name
from app.models import Company


class SiteRegistryError(Exception):
    pass


def load_site_mapping(path: Path, session: Session) -> dict[UUID, str]:
    try:
        payload = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SiteRegistryError("jobhunt_site_registry_invalid") from error
    if not isinstance(payload, dict):
        raise SiteRegistryError("jobhunt_site_registry_invalid")
    mapping: dict[UUID, str] = {}
    used_sites: set[str] = set()
    for company_name, site in payload.items():
        if not isinstance(company_name, str) or not isinstance(site, str) or not site.strip():
            raise SiteRegistryError("jobhunt_site_registry_invalid")
        company_ids = tuple(
            session.scalars(
                select(Company.id).where(
                    or_(
                        Company.canonical_name == company_name,
                        Company.normalized_name == normalize_name(company_name),
                    )
                )
            )
        )
        site_key = site.strip()
        if len(company_ids) != 1 or site_key in used_sites:
            raise SiteRegistryError("jobhunt_site_unmapped")
        mapping[company_ids[0]] = site_key
        used_sites.add(site_key)
    return mapping
