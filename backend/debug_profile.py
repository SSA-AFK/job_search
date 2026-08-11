"""Debug: test profile stage for companies that failed with invalid_output at profile stage."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pydantic import ValidationError

from app.core.config import settings
from app.ingestion.contracts import ProviderQuery
from app.ingestion.extraction.client import OpenAICompatibleLlmClient
from app.ingestion.extraction.prompts import build_prompt
from app.ingestion.extraction.schemas import ExtractionBatch, CompanyRef
from app.ingestion.providers.zhihu import ZhihuGlobalSearchProvider
from app.core.normalization import normalize_name

_EXTRACTION_SCHEMA = ExtractionBatch.model_json_schema()


async def test_profile(query_name: str) -> None:
    print(f"\n{'='*60}")
    print(f"Testing profile: {query_name}")

    provider = ZhihuGlobalSearchProvider(
        enabled=True, access_secret=settings.zhihu_access_secret
    )
    query = ProviderQuery(query=query_name, website=None, allowed_hosts=frozenset(), max_results=10)
    result = await provider.search(query)
    docs = result.documents
    if not docs:
        print("  No docs")
        return

    evidence_ids, prompt = build_prompt("discover", docs)
    llm = OpenAICompatibleLlmClient(
        base_url=settings.openai_compatible_base_url,
        model=settings.openai_compatible_model,
        api_key=settings.openai_compatible_api_key,
        timeout_seconds=60.0,
    )

    # discover
    try:
        response = await llm.complete(prompt, response_schema=_EXTRACTION_SCHEMA)
        payload = json.loads(response)
        batch = ExtractionBatch.model_validate(payload, context={"allowed_evidence_ids": evidence_ids})
        target = normalize_name(query_name)
        matches = [c for c in batch.companies if normalize_name(c.name) == target]
        if len(matches) != 1:
            print(f"  Discover: {len(matches)} matches (skip)")
            return
        company_ref = CompanyRef(name=matches[0].name, website=matches[0].website)
        print(f"  Discover OK: {company_ref.name}")
    except ValidationError as e:
        errors = e.errors()
        print(f"  Discover FAILED (ValidationError): {len(errors)} errors")
        for err in errors[:5]:
            loc = err.get("loc")
            typ = err.get("type")
            msg = err.get("msg", "")[:120]
            inp = err.get("input")
            inp_str = str(inp)[:150] if inp is not None else "None"
            print(f"    loc={loc} type={typ}")
            print(f"    msg={msg}")
            print(f"    input={inp_str}")
        return
    except json.JSONDecodeError as e:
        print(f"  Discover JSON decode error: {e}")
        print(f"  Response[:500]: {response[:500]}")
        return
    except Exception as e:
        print(f"  Discover FAILED: {type(e).__name__}: {e}")
        return

    # profile
    evidence_ids, prompt = build_prompt("profile", docs, company_ref)
    response = await llm.complete(prompt, response_schema=_EXTRACTION_SCHEMA)
    print(f"  Profile response length: {len(response)}")

    try:
        payload = json.loads(response)
        batch = ExtractionBatch.model_validate(payload, context={"allowed_evidence_ids": evidence_ids})
        print(f"  Profile OK: {len(batch.profiles)} profiles, {len(batch.filings)} filings")
    except ValidationError as e:
        errors = e.errors()
        print(f"  Profile FAILED: {len(errors)} errors")
        for err in errors[:5]:
            loc = err.get("loc")
            typ = err.get("type")
            msg = err.get("msg", "")[:120]
            inp = err.get("input")
            inp_str = str(inp)[:150] if inp is not None else "None"
            print(f"    loc={loc} type={typ}")
            print(f"    msg={msg}")
            print(f"    input={inp_str}")
    except json.JSONDecodeError as e:
        print(f"  JSON decode error: {e}")
        print(f"  Response[:500]: {response[:500]}")


async def main() -> None:
    companies = [
        "中航信移动科技股份有限公司",
        "云语智能科技(杭州)有限公司",
        "中移支付有限公司",
    ]
    for name in companies:
        await test_profile(name)


if __name__ == "__main__":
    asyncio.run(main())
