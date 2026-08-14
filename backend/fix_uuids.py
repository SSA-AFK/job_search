"""修复非法 UUID 形式的表主键 id"""
import re
import uuid
import sqlite3

DB = 'company_search.db'

# 合法 UUID 形式：8-4-4-4-12
UUID_RE = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')

def is_valid_uuid(s: str) -> bool:
    if s is None: return False
    return bool(UUID_RE.match(s))

def stable_uuid(prefix: str, key: str) -> str:
    """稳定生成 id"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'{prefix}:{key}'))

conn = sqlite3.connect(DB)
cur = conn.cursor()

# ============ company_aliases ============
print('=== company_aliases 修复前 ===')
rows = cur.execute("SELECT id, company_id, alias FROM company_aliases").fetchall()
bad = [r for r in rows if not is_valid_uuid(r[0])]
print(f'  总行数 {len(rows)}，非法 id 行数 {len(bad)}')
for r in bad[:10]:
    print(f'    {r[0]}')

# 更新为合法 UUID：基于 alias+company_id 生成
for (old_id, cid, alias) in list(bad):
    new_id = stable_uuid('company-alias', f'{cid}|{alias}')
    cur.execute("UPDATE company_aliases SET id = ? WHERE id = ?", (new_id, old_id))
print(f'  已更新 company_aliases.id: {len(bad)} 条')

# ============ 顺带检查常见其他表的 id：source_documents, company_ranking_signals, ranking_collection_runs, etc ============
TABLES_ID_COLS = [
    ('source_documents', 'id'),
    ('company_ranking_signals', 'id'),
    ('ranking_collection_runs', 'id'),
    ('companies', 'id'),
    ('jobs', 'id'),
    ('company_identities', 'id'),
    ('company_verifications', 'id'),
    ('profile_fields', 'id'),
    ('filings', 'id'),
]

for tbl, col in TABLES_ID_COLS:
    try:
        rows = cur.execute(f"SELECT {col} FROM {tbl}").fetchall()
    except sqlite3.OperationalError as e:
        # 表不存在或列不存在：跳过
        continue
    bad_ids = [r[0] for r in rows if not is_valid_uuid(r[0])]
    if bad_ids:
        print(f'⚠️ {tbl}.{col}: 非法 id {len(bad_ids)} 条，示例: {bad_ids[:3]}')
    else:
        print(f'✅ {tbl}.{col}: 所有 id 合法（共 {len(rows)} 条）')

conn.commit()
conn.close()
print('\n✅ 修复完成')
