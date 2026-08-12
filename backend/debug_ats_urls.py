"""Debug: print discovered ATS career URLs for a company and probe each host."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.ingestion.runtime import build_ingestion_orchestrator
from app.ingestion.orchestrator import _ATS_URL_PATTERNS  # noqa
from app.core.database import SessionLocal
from app.core.config import settings
from app.ingestion.production import create_runtime_components
from app.ingestion.contracts import ProviderQuery
from app.ingestion import contracts

QUERY = sys.argv[1] if len(sys.argv) > 1 else "字节跳动"


async def main() -> None:
    components = create_runtime_components(settings)
    # discovery via entry discovery service
    serper = next((p for p in components.providers if getattr(p, "name", None) == "serper"), None)
    from app.ingestion.entry_discovery.service import EntryDiscoveryService
    from app.ingestion.orchestrator import _company_name_pool_from_request
    svc = EntryDiscoveryService(serper_provider=serper)
    result = await svc.discover(_company_name_pool_from_request(QUERY))
    print("candidates:")
    for c in result.candidates:
        print(f"  url={c.url!r} platform={c.platform} conf={c.overall_confidence:.2f} high={c.is_high_confidence(svc._threshold)}")
    print("high_confidence:")
    for c in result.high_confidence:
        print(f"  url={c.url!r} platform={c.platform}")
    print("diagnostics:", result.diagnostics)


if __name__ == "__main__":
    asyncio.run(main())