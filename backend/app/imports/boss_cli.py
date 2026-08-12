import argparse
import json
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.imports.boss_json import BossImportError, load_boss_json
from app.job_enumeration.manual_batch import ManualBossImportService


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a manually captured BOSS JSON batch")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    if args.database_url == settings.database_url:
        parser.error("--database-url must be explicit and must not equal the configured default")
    try:
        batch = load_boss_json(args.input)
    except (BossImportError, OSError) as error:
        parser.error(str(error))
    engine = create_engine(args.database_url)
    with Session(engine, expire_on_commit=False) as session:
        summary = ManualBossImportService(session).import_file(batch)
    print(json.dumps(asdict(summary), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
