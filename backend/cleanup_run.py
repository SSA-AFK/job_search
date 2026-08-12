"""Delete stale byte dance request/run from DB."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.core.database import SessionLocal
from app.models import collection as m

s = SessionLocal()
try:
    n = (
        s.query(m.CollectionRequest)
        .filter(m.CollectionRequest.normalized_query == "字节跳动")
        .delete(synchronize_session=False)
    )
    s.commit()
    print("deleted requests:", n)
finally:
    s.close()