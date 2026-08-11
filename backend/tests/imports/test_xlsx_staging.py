from pathlib import Path

import pytest
from openpyxl import Workbook

from app.imports.xlsx_staging import CohortWorkbookError, read_tianyancha_cohort


def _workbook(path: Path, *, title: str, names: list[str]) -> Path:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = title
    worksheet.append(["声明"])
    worksheet.append(["公司名称"])
    for name in names:
        worksheet.append([name])
    workbook.save(path)
    return path


def test_reader_returns_exactly_rows_three_through_twenty_two(tmp_path: Path) -> None:
    workbook = _workbook(tmp_path / "companies.xlsx", title="高级搜索", names=[f"公司{i}" for i in range(1, 22)])

    cohort = read_tianyancha_cohort(workbook)

    assert [item.source_row for item in cohort] == list(range(3, 23))
    assert [item.canonical_name for item in cohort] == [f"公司{i}" for i in range(1, 21)]


def test_reader_rejects_missing_sheet_or_blank_selected_company(tmp_path: Path) -> None:
    missing_sheet = _workbook(tmp_path / "other.xlsx", title="Sheet1", names=[f"公司{i}" for i in range(20)])
    with pytest.raises(CohortWorkbookError, match="高级搜索"):
        read_tianyancha_cohort(missing_sheet)

    blank_name = _workbook(tmp_path / "blank.xlsx", title="高级搜索", names=["公司1", "", *[f"公司{i}" for i in range(3, 21)]])
    with pytest.raises(CohortWorkbookError, match="rows 3-22"):
        read_tianyancha_cohort(blank_name)
