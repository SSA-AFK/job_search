"""Debug: run the AtsProvider directly against a single byte dance list URL and time it."""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.ingestion.contracts import ProviderQuery
from app.ingestion.production import create_runtime_components
from app.core.config import settings

URL = sys.argv[1] if len(sys.argv) > 1 else "https://jobs.bytedance.com/experienced/position"


async def main() -> None:
    components = create_runtime_components(settings)
    ats = next(p for p in components.providers if getattr(p, "name", None) == "ats")
    started = time.monotonic()
    result = await ats.search(ProviderQuery(query="字节跳动", website=URL))
    elapsed = time.monotonic() - started
    print("=" * 60)
    print(f"url            : {URL}")
    print(f"elapsed_seconds: {elapsed:.3f}")
    print(f"warnings       : {result.warnings}")
    for stat in result.stats:
        print(f"stat           : platform={stat.platform} parsed_jobs={stat.parsed_jobs} error={stat.error_code}")
    print(f"parsed_jobs    : {len(result.parsed_jobs)}")
    for job in result.parsed_jobs[:5]:
        print(f"  - {job.title} | {job.city} | {job.employment_type}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
