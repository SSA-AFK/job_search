"""补 DeepSeek 公司详情页必要的中文字段 + 正确枚举值"""
import sqlite3
from datetime import datetime

DB = 'company_search.db'
CID = '11111111-1111-1111-1111-111111111111'

now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

conn = sqlite3.connect(DB)
cur = conn.cursor()

cur.execute("""
UPDATE companies SET
  industry             = ?,          -- 原为 "Artificial Intelligence"
  description          = ?,
  province             = ?,          -- 原为 NULL
  headquarters         = ?,
  funding_stage        = ?,          -- 原为 "unknown" → A轮对应 series_a
  scale                = ?,          -- 原为 "one_to_49" → 真实规模 200-499
  updated_at           = ?
WHERE id = ?
""", (
    '人工智能',
    '大语言模型研发商，专注于通用人工智能（AGI）的基础技术研究与应用落地；'
    '自主研发了DeepSeek通用对话助手、企业级模型API、代码模型（Coder）、推理模型（R1）、数学模型等系列产品，'
    '在公开榜单上多项能力表现跻身全球前沿水平；2024年完成A轮融资，投后估值跻身独角兽行列；'
    '创始团队拥有深厚的大模型算法与工程积累，北京为研发总部，杭州为工商注册总部，上海、深圳等地设办公地点。',
    '浙江省',
    '杭州市',
    'series_a',
    '200_to_499',
    now,
    CID,
))

assert cur.rowcount == 1, f"UPDATE 行数={cur.rowcount}，应=1"
conn.commit()

# 展示修改后结果
row = cur.execute(
    "SELECT canonical_name, industry, description, province, headquarters, city, funding_stage, scale, established_at, founded_year, registered_capital, paid_in_capital, insured_employee_count, company_type, industry_sector, industry_middle FROM companies WHERE id = ?",
    (CID,)
).fetchone()
cols = [d[0] for d in cur.description]
for c, v in zip(cols, row):
    print(f'  {c} = {v}')

conn.close()
print('\n✅ DeepSeek 字段更新完成')
