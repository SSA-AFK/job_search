import argparse
from pathlib import Path

from app.core.database import SessionLocal
from app.seed.importer import import_seed
from app.seed.schema import SeedPayload


def main() -> None:
    parser = argparse.ArgumentParser(description="Import versioned company seed data")
    parser.add_argument("seed_path", type=Path)
    args = parser.parse_args()

    payload = SeedPayload.model_validate_json(args.seed_path.read_text(encoding="utf-8"))
    with SessionLocal() as session:
        summary = import_seed(session, payload)
    print(
        f"companies_created={summary.companies_created} "
        f"companies_updated={summary.companies_updated} "
        f"jobs_created={summary.jobs_created} "
        f"sources_created={summary.sources_created}"
    )


if __name__ == "__main__":
    main()
