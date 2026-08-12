import json
from datetime import UTC, datetime

from app.imports.boss_json import load_boss_json


def test_loads_manual_boss_export_without_starting_browser(tmp_path) -> None:
    path = tmp_path / "boss.json"
    path.write_text(
        json.dumps(
            [
                {
                    "job_id": "job-1",
                    "job_name": "AI Engineer",
                    "job_url": "https://www.zhipin.com/job_detail/job-1.html",
                    "company_name": "Acme",
                    "brand_id": "brand-1",
                    "city": "上海",
                }
            ]
        ),
        encoding="utf-8",
    )

    batch = load_boss_json(path, observed_at=datetime(2026, 8, 12, tzinfo=UTC))

    assert len(batch.records) == 1
    assert batch.records[0].company_name == "Acme"
    assert batch.records[0].brand_id == "brand-1"
    assert batch.records[0].job.source_provider == "boss_manual"
    assert len(batch.fingerprint) == 64


def test_invalid_rows_are_rejected_without_creating_companies(tmp_path) -> None:
    path = tmp_path / "boss.json"
    path.write_text(json.dumps([{"job_id": "missing-company"}]), encoding="utf-8")

    batch = load_boss_json(path)

    assert batch.records == ()
    assert batch.rejected_records == 1
