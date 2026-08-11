from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.imports.service import import_cohort
from app.models import Base, Company, ImportItem


def _workbook(path: Path) -> Path:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "高级搜索"
    worksheet.append(["声明"])
    worksheet.append(["公司名称"])
    for index in range(20):
        worksheet.append([f"公司{index + 1}"])
    workbook.save(path)
    return path


def test_import_persists_source_provenance_and_is_idempotent(tmp_path: Path) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    workbook = _workbook(tmp_path / "companies.xlsx")

    with Session(engine) as session:
        first = import_cohort(session, workbook)
        second = import_cohort(session, workbook)

        assert first.companies_created == 20
        assert first.items_imported == 20
        assert second.companies_created == 0
        assert second.companies_matched == 20
        assert session.scalar(select(func.count()).select_from(Company)) == 20
        assert session.scalar(select(func.count()).select_from(ImportItem)) == 20
        assert {item.source_row for item in session.scalars(select(ImportItem))} == set(range(3, 23))
