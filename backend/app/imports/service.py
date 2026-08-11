from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.imports.xlsx_staging import WORKSHEET_NAME, read_tianyancha_cohort
from app.models import Company, ImportBatch, ImportItem


@dataclass(frozen=True)
class CohortImportSummary:
    companies_created: int
    companies_matched: int
    items_imported: int


def import_cohort(session: Session, workbook_path: Path) -> CohortImportSummary:
    staged = read_tianyancha_cohort(workbook_path)
    created = 0
    matched = 0
    imported = 0
    with session.begin():
        batch = session.scalar(select(ImportBatch).where(ImportBatch.workbook_filename == workbook_path.name, ImportBatch.worksheet_name == WORKSHEET_NAME))
        if batch is None:
            batch = ImportBatch(workbook_filename=workbook_path.name, worksheet_name=WORKSHEET_NAME)
            session.add(batch)
            session.flush()
        for item in staged:
            company = session.scalar(select(Company).where(Company.normalized_name == item.normalized_name))
            if company is None:
                company = Company(canonical_name=item.canonical_name, normalized_name=item.normalized_name)
                session.add(company)
                session.flush()
                created += 1
            else:
                matched += 1
            existing = session.scalar(select(ImportItem).where(ImportItem.import_batch_id == batch.id, ImportItem.source_row == item.source_row))
            if existing is None:
                session.add(ImportItem(import_batch_id=batch.id, company_id=company.id, source_row=item.source_row, source_name=item.canonical_name, normalized_source_name=item.normalized_name))
                imported += 1
    return CohortImportSummary(created, matched, imported)
