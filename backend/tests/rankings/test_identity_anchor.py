from hashlib import sha256
from types import SimpleNamespace

import pytest

from app.rankings.identity_anchor import (
    IdentityAnchorError,
    legal_search_key,
    verify_registration_name,
    verify_search_candidate,
)


LEGAL_NAME = "杭州深度求索人工智能基础技术研究有限公司"


def test_search_anchor_requires_one_exact_active_legal_company() -> None:
    match = verify_search_candidate(
        {
            "items": [
                {
                    "name": LEGAL_NAME,
                    "id": 6316079035,
                    "creditCode": "91330105MACPN4X08Y",
                    "regStatus": "存续",
                },
                {"name": "北京深度求索人工智能基础技术研究有限公司"},
            ]
        },
        LEGAL_NAME,
    )

    assert match["name"] == LEGAL_NAME
    assert sha256(match["creditCode"].encode()).hexdigest() != match["creditCode"]


def test_unverified_brand_cannot_be_used_as_tianyancha_search_key() -> None:
    company = SimpleNamespace(
        canonical_name="DeepSeek（深度求索）",
        legal_name=None,
        identity_anchor_status="unverified",
    )

    with pytest.raises(IdentityAnchorError):
        legal_search_key(company)  # type: ignore[arg-type]


def test_registration_response_must_match_anchored_legal_name() -> None:
    with pytest.raises(IdentityAnchorError):
        verify_registration_name(
            {"sources": {"base": {"empty": False, "name": "其他公司"}}},
            LEGAL_NAME,
        )
