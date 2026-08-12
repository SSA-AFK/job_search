from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session

from app.models import (
    Base,
    Company,
    CompanyProfileField,
    CompanyRankingSnapshot,
    RankingPilotMember,
    SourceDocument,
    VerificationStatus,
)
from app.rankings.service import import_ai_pilot, pilot_report, rescore_ai_pilot


def _workbook(path: Path) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "高级搜索"
    sheet.append(["声明"])
    sheet.append(
        [
            "公司名称",
            "登记状态",
            "企业规模",
            "成立日期",
            "所属省份",
            "所属城市",
            "国标行业大类",
            "统一社会信用代码",
            "网址",
            "天眼评分",
            "参保人数",
            "参保人数所属年报",
            "经营范围",
            "注册资本",
            "实缴资本",
            "所属区县",
            "企业(机构)类型",
            "国标行业门类",
            "国标行业中类",
        ]
    )
    for number in range(110):
        sheet.append(
            [
                f"公司{number}",
                "存续",
                "小型",
                "2020-01-01",
                f"省{number % 2}",
                "市",
                f"行业{number % 3}",
                f"91310000{number:010d}",
                "-",
                str(60 + number % 30),
                str(20 + number),
                "2025",
                "人工智能软件开发",
                "1000万人民币",
                "500万人民币",
                "海淀区",
                "有限责任公司",
                "信息传输、软件和信息技术服务业",
                "软件开发",
            ]
        )
    workbook.save(path)
    return path


def test_internal_pilot_persists_only_minimized_data_and_zero_evidence_scores(
    tmp_path: Path,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        first = import_ai_pilot(session, _workbook(tmp_path / "companies.xlsx"))
        second = import_ai_pilot(session, tmp_path / "companies.xlsx")

        assert first.eligible_candidates == 110
        assert first.members_selected == 100
        assert second.companies_created == 0
        assert session.scalar(select(func.count()).select_from(Company)) == 100
        assert session.scalar(select(func.count()).select_from(RankingPilotMember)) == 100
        company = session.scalar(select(Company))
        assert company is not None
        assert company.registered_capital == "1000万人民币"
        assert company.business_scope == "人工智能软件开发"
        snapshots = tuple(session.scalars(select(CompanyRankingSnapshot)))
        assert len(snapshots) == 100
        assert all(snapshot.total_score == 0 and not snapshot.is_eligible for snapshot in snapshots)
        report = pilot_report(session, first.pilot_id)
        assert len(report) == 100
        assert {"company_name", "component_scores", "missing_fields"} <= report[0].keys()
        assert {
            "company_stage",
            "raw_component_scores",
            "stage_percentiles",
            "evidence_coverage",
            "eligibility_reasons",
        } <= report[0].keys()
        assert "source_row" not in report[0]

        session.commit()
        rescore_ai_pilot(session, first.pilot_id)
        rescored = tuple(session.scalars(select(CompanyRankingSnapshot)))
        assert all(snapshot.evidence_coverage["ai_core"] for snapshot in rescored)
        assert all(not snapshot.is_eligible for snapshot in rescored)
        assert all(
            snapshot.eligibility_reasons == ["insufficient_component_coverage"]
            for snapshot in rescored
        )

    columns = {column["name"] for column in inspect(engine).get_columns("ranking_pilot_members")}
    assert "source_identity_hash" in columns
    assert not {"phone", "email", "legal_representative", "address"} & columns


def test_rescore_replaces_zero_snapshot_after_verified_public_evidence(tmp_path: Path) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        summary = import_ai_pilot(session, _workbook(tmp_path / "companies.xlsx"), sample_size=1)
        company_id = session.scalar(select(RankingPilotMember.company_id))
        assert company_id is not None
        now = datetime(2026, 8, 11, tzinfo=UTC)
        document = SourceDocument(
            provider="official_news",
            external_id=None,
            url="https://example.com/products",
            title="Official products",
            text_excerpt="AI products and customer cases",
            content_hash=sha256(b"official evidence").hexdigest(),
            authority_level=1,
            published_at=None,
            fetched_at=now,
        )
        session.add(document)
        session.flush()
        for key in (
            "ai.core_level",
            "ai.products",
            "ai.market_proofs",
            "ai.growth_events",
            "ai.technology_signals",
        ):
            session.add(
                CompanyProfileField(
                    company_id=company_id,
                    field_key=key,
                    value={"source": "official"},
                    source_document_id=document.id,
                    verification_status=VerificationStatus.VERIFIED,
                    collected_at=now,
                )
            )
        session.commit()

        rescore_ai_pilot(session, summary.pilot_id)
        snapshot = session.scalar(select(CompanyRankingSnapshot))
        assert snapshot is not None
        assert snapshot.total_score == 100
        assert snapshot.is_eligible
        assert snapshot.missing_fields == []
