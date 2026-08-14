"""直接重现 HTTP 500：调用 CompanyService.get_detail 拿完整 traceback"""
import sys
import enum
if not hasattr(enum, 'StrEnum'):
    class _StrEnum(str, enum.Enum):
        def __str__(self): return self.value
    enum.StrEnum = _StrEnum
    sys.modules['enum'] = enum
import datetime as _dtm
if not hasattr(_dtm, 'UTC'):
    from datetime import timezone
    _dtm.UTC = timezone.utc
sys.modules['datetime'] = _dtm

from uuid import UUID
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, 'd:/tools_dev/company_search/backend')
engine = create_engine("sqlite:///./company_search.db")

with Session(engine) as session:
    from app.companies.repository import CompanyRepository
    from app.companies.service import CompanyService
    from app.cache.redis import configured_company_cache
    from app.core.config import settings

    svc = CompanyService(
        CompanyRepository(session),
        cache=configured_company_cache(None),  # Redis 不需要
        job_total_limit=100,
    )
    cid = UUID('11111111-1111-1111-1111-111111111111')
    try:
        detail = svc.get_detail(cid)
        print('✅ OK，没有异常！')
        print(f'canonical_name = {detail.canonical_name}')
        print(f'ranking_score = {detail.ranking_score}')
        print(f'aliases = {detail.aliases}')
        print(f'ranking_components = {detail.ranking_components.model_dump()}')
        print(f'ranking_signals 数 = {len(detail.ranking_signals)}')
        print(f'profile_fields 数 = {len(detail.profile_fields)}')
    except Exception as e:
        print(f'❌ 异常：{type(e).__name__}: {e}')
        import traceback
        traceback.print_exc()
