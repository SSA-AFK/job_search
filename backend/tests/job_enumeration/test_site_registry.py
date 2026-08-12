import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.job_enumeration.site_registry import SiteRegistryError, load_site_mapping
from app.models import Base, Company


def test_loads_reviewed_unique_company_mapping(tmp_path) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        company = Company(canonical_name="Acme", normalized_name="acme")
        session.add(company)
        session.commit()
        path = tmp_path / "sites.json"
        path.write_text(json.dumps({"Acme": "acme"}), encoding="utf-8")

        assert load_site_mapping(path, session) == {company.id: "acme"}


def test_unknown_company_mapping_is_rejected(tmp_path) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    path = tmp_path / "sites.json"
    path.write_text(json.dumps({"Unknown": "unknown"}), encoding="utf-8")

    with Session(engine) as session, pytest.raises(
        SiteRegistryError, match="jobhunt_site_unmapped"
    ):
        load_site_mapping(path, session)
