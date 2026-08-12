from datetime import date

from app.rankings.gap_plan import EnrichmentCategory
from app.rankings.tianyancha.projectors import project_response

WINDOW = date(2023, 8, 12)


def test_patent_projector_keeps_only_recent_ai_patents_and_drops_people_and_addresses() -> None:
    payload = {
        "items": [
            {
                "patentName": "商业查询大模型训练方法",
                "applicationTime": "2025-10-16",
                "patentType": "发明专利",
                "patentStatus": "实质审查",
                "mainCatNum": "G06F16/33",
                "inventor": "某个人",
                "address": "完整地址",
            },
            {"patentName": "普通支架", "applicationTime": "2025-01-01"},
        ]
    }

    signals = project_response(
        EnrichmentCategory.INTELLECTUAL_PROPERTY,
        payload,
        company_name="示例公司",
        window_start=WINDOW,
    )

    assert len(signals) == 1
    assert signals[0].value["title"] == "商业查询大模型训练方法"
    assert "inventor" not in signals[0].value
    assert "address" not in signals[0].value


def test_bid_projector_requires_company_to_be_the_winner() -> None:
    payload = {
        "items": [
            {
                "title": "人工智能项目",
                "publishTime": "2026-01-08",
                "enterpriseIdentity": "中标方",
                "bidWinner": "示例公司",
                "purchaser": "采购单位",
                "bidAmount": "1000000",
                "bidUrl": "https://vendor.invalid/raw",
            },
            {
                "title": "只被提及",
                "publishTime": "2026-01-09",
                "enterpriseIdentity": "被提及",
                "bidWinner": "其他公司",
            },
        ]
    }

    signals = project_response(
        EnrichmentCategory.MARKET_VALIDATION,
        payload,
        company_name="示例公司",
        window_start=WINDOW,
    )

    assert len(signals) == 1
    assert "bidAmount" not in signals[0].value
    assert "bidUrl" not in signals[0].value


def test_risk_projector_excludes_ordinary_litigation_and_related_company_risk() -> None:
    payload = {
        "relationRiskNotes": [{"riskType": "严重违法", "relatedCompany": "子公司"}],
        "toolRisks": [
            {"riskType": "裁判文书", "riskLevel": "警示", "count": 12},
            {"riskType": "严重违法", "riskLevel": "高风险", "count": 1},
        ],
    }

    signals = project_response(
        EnrichmentCategory.MATERIAL_RISK,
        payload,
        company_name="示例公司",
        window_start=WINDOW,
    )

    assert len(signals) == 1
    assert signals[0].value["risk_type"] == "严重违法"
    assert "relatedCompany" not in signals[0].value
