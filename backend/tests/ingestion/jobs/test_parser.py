# backend/tests/ingestion/jobs/test_parser.py
from pathlib import Path

from app.ingestion.jobs.contracts import AtsParseStatus
from app.ingestion.jobs.parser import parse_html_job_list

_DATA = Path(__file__).parents[3] / "data" / "ats"  # parents[3] = backend/ (see Amendment 4)


def test_parser_extracts_feishu_candidates_from_fixture() -> None:
    html = (_DATA / "feishu_list.html").read_text(encoding="utf-8")
    result = parse_html_job_list(html, platform="feishu")
    assert result.status == AtsParseStatus.SUCCEEDED
    assert len(result.candidates) > 0
    first = result.candidates[0]
    assert first.title
    assert str(first.url).startswith("http")


def test_parser_extracts_moka_candidates_from_fixture() -> None:
    html = (_DATA / "moka_list.html").read_text(encoding="utf-8")
    result = parse_html_job_list(html, platform="moka")
    assert result.status == AtsParseStatus.SUCCEEDED
    assert len(result.candidates) > 0


def test_parser_returns_failed_on_empty_html() -> None:
    result = parse_html_job_list("", platform="feishu")
    assert result.status == AtsParseStatus.FAILED
    assert result.error_code == "parse_failed"
    assert result.observed_count == 0


def test_parser_generic_platform_extracts_anchor_cards() -> None:
    html = '<div><a class="job-card" href="https://careers.example.com/j/1">Engineer</a></div>'
    result = parse_html_job_list(html, platform="generic")
    assert result.status == AtsParseStatus.SUCCEEDED
    assert len(result.candidates) == 1


def test_parser_never_raises_on_malformed_input() -> None:
    result = parse_html_job_list("<<<not html>>>", platform="feishu")
    assert result.status in {AtsParseStatus.PARTIAL, AtsParseStatus.FAILED}
