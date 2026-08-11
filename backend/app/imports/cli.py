import argparse
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.schema import MetaData

from app.core.config import settings
from app.imports.service import import_cohort
from app.models import *
from app.models.base import Base


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the fixed 20-company local pilot cohort")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--workbook", required=True, type=Path)
    args = parser.parse_args()
    if args.database_url == settings.database_url:
        parser.error("--database-url must not be the configured default database")
    if not args.database_url.startswith("sqlite:///"):
        parser.error("the pilot importer accepts an explicit SQLite database URL only")
    engine = create_engine(args.database_url)
    metadata = Base.metadata
    assert isinstance(metadata, MetaData)
    metadata.create_all(engine)
    with Session(engine) as session:
        summary = import_cohort(session, args.workbook)
    print(json.dumps({"companies_created": summary.companies_created, "companies_matched": summary.companies_matched, "items_imported": summary.items_imported}, ensure_ascii=False))


if __name__ == "__main__":
    main()
