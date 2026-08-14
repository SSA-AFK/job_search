# 职位获取详细路径（ATS Playwright + BOSS CDP）

> 状态：当前生产管线**完全使用结构化路径**，两条开源抓取通道均直接解析 HTML / JSON 响应后写入 `normalize_job → JobDeduplicator → PersistenceService.merge_or_insert`。
> 旧的 `CrewExtractor.extract_jobs` LLM 分支已于 2026-08-12 在 [orchestrator.py](file:///d:/tools_dev/company_search/backend/app/ingestion/orchestrator.py) 中整体删除。

---

## 0 · 共用出口（两条路径汇合）

两条路径产出的「职位原始记录」最终都会进入以下统一链路，对应代码：

| 步骤 | 入口文件 | 关键函数 / 类 |
|---|---|---|
| ① 字段标准化 | `backend/app/ingestion/normalization/job.py` | `normalize_job(job_id, company_id, city, raw)` |
| ② 规则去重 | `backend/app/ingestion/deduplication/job.py` | `JobDeduplicator.dedupe(batch, prior_jobs)` |
| ③ 合并入库 | `backend/app/ingestion/persistence/service.py` | `PersistenceService(merge_mode=MERGE_OR_INSERT).persist(batch)` |
| ④ 触发评分 | `backend/app/rankings/service.py` | `rescore_ai_pilot(session, pilot_id)` |

归一化和去重的核心规则：

- **`normalize_job`**：把原始 salary 字符串（如 `"20-40K"` / `"15-25K·14薪"` / `"面议"`）解析成 `salary_min / salary_max / salary_unit / salary_is_negotiable`；`experience` / `degree` / `city` 映射到标准枚举；`source_url` 和 `source_external_id` 必填（用于后续去重与更新）。
- **`JobDeduplicator`**：
  1. `company_id + source_external_id` 强匹配（同一平台同一职位 ID 立即算重复）；
  2. 否则走 **`difflib.SequenceMatcher` 标题相似度 ≥ 0.85 且 `城市完全匹配`** → 判重复，保留较新的一条。
- **`merge_or_insert`**：命中去重规则 → `UPDATE`（更新 salary / updated_at / source_url 等可变字段）；否则 `INSERT`。

---

## 1 · 路径 A：大厂官网 ATS Provider（Playwright 开源）

### 1.1 负责文件

- Provider 主入口：`backend/app/ingestion/providers/ats_provider.py`
- Playwright 工具封装：`backend/app/ingestion/providers/playwright_kit.py`
- 关联调度：`backend/app/ingestion/orchestrator.py` 中 `_collect_ats_directly(...)`

### 1.2 抓取步骤

```
① 匹配 ATS 域名模式
    │
    ├─ *.greenhouse.io   （Greenhouse ATS）
    ├─ *.lever.co        （Lever ATS）
    ├─ *.workday.com     （Workday）
    ├─ *.ashbyhq.com     （Ashby）
    ├─ careers.<主域>    （官网自建职位页）
    └─ jobs.<主域> / job.<主域> / join.<主域>
    ↓
② Playwright 无头浏览器启动（headless=True，browser=chromium）
    │
    ├─ 访问  /careers  /jobs  /open-roles  /careers/list  候选 URL
    ├─ 滚动到底部 + 点击「Load more」「View more」按钮直到不再出新职位
    └─ 有分页的页面（?page=N / ?p=N）自动逐页翻完
    ↓
③ HTML / JSON 直接解析（三种模式，按页面结构自动选择）
    │
    ├─ [模式 1：LD+JSON]
    │   抓取 <script type="application/ld+json"> 节点，
    │   解析 "@type": "JobPosting" 数组 → title / jobLocation /
    │   baseSalary / identifier / url 直接映射。
    │
    ├─ [模式 2：列表 DOM]
    │   选择 li[data-job-id] / .job-item / a[href*="/jobs/"] 这类
    │   稳定的职位条目容器 → 逐元素 extract text + href。
    │
    └─ [模式 3：ATS REST JSON]
        直接对 Greenhouse / Lever 的公开 JSON API 发 GET：
        - Greenhouse: /api/v1/applicant_tracking/jobs?content=true
        - Lever:      /v1/postings?group=team
        → 解析 PositionInfo 数组。
    ↓
④ 字段映射 → 原始 job 记录
    ┌───────────────────────────┬──────────────────────────────────┐
    │ 输出字段                  │ 来源                             │
    ├───────────────────────────┼──────────────────────────────────┤
    │ source                    │ "ats"                            │
    │ source_external_id        │ data-job-id / ld+json.identifier │
    │ title                     │ <h..>文本 / ld+json.title        │
    │ city                      │ location / jobLocation.address   │
    │ salary_min / salary_max   │ baseSalary.value.minValue / max  │
    │ source_url                │ href 全路径                      │
    │ company_id                │ 由 ATS brand → Company 映射得出  │
    │ description(可选)         │ 职位详情页的 <div class="desc">   │
    └───────────────────────────┴──────────────────────────────────┘
    ↓
⑤ 写 CompanyProfileField.ats_provider_ids（跳过 Candidate/Extraction 分支）
    例如：{"greenhouse": "字节跳动-抖音", "lever": "bytedance-eng"}
    然后直接走 normalize_job → 去重 → 入库；ATS 未命中不再回退到
    LLM 提取（orchestrator 返回 error_code="ats_entry_discovery_pending"，
    extractor.discover / extract_profile / extract_jobs 整段 LLM 分支已删除）。
```

### 1.3 典型成功匹配示例

| 雇主官网 | 匹配模式 | 实际入口 |
|---|---|---|
| ByteDance / 字节 | Greenhouse ATS | `jobs.bytedance.com/api/v1/applicant_tracking/jobs` |
| OpenAI（国内镜像路径） | Ashby + careers.*  | `openai.com/careers` → LD+JSON 列表 |
| 某未自研系统的大厂 | Lever.co | `jobs.lever.co/<brand>` → 模式 2 DOM 选择器 |

---

## 2 · 路径 B：BOSS 直聘 CDP Provider（开源品牌 ID 工具）

### 2.1 负责文件

- Provider 主入口：`backend/app/ingestion/providers/boss_cdp_provider.py`
- 城市枚举表：`backend/app/ingestion/providers/boss_cdp_cities.py`（`CITY_CODE: str -> CITY_NAME: str` 字典）
- 关联调度：`backend/app/ingestion/orchestrator.py` 中 `_collect_boss_cdp(...)`
- 归一化入口：`backend/app/ingestion/normalization/job.py` 的 `normalize_job(job_id=None, city=search_city, raw=position_info_resp)` 分支

### 2.2 抓取步骤

```
① 获取 brandid（BOSS 品牌页 ID）
    │
    ├─ 手工取：打开 boss.com → 搜索公司名 → 点进公司主页
    │          URL 形如 https://www.zhipin.com/gongsi/brandid-9f3b7xxxx.html
    │          其中 brandid=9f3b7xxxx（8~16 位 hex + 数字混合）
    └─ 或用 Excel Pilot 表字段 boss_brandid 直接传入（推荐）
    ↓
② bossjob_search_city × 分页 枚举（15 个重点城市）
    │
    ├─ 城市枚举表（boss_cdp_cities.py）
    │    北京 101010100  上海 101020100  深圳 101280600
    │    广州 101280100  杭州 101210100  成都 101270100
    │    南京 101190100  武汉 101200100  西安 101110100
    │    苏州 101190400  重庆 101040100  天津 101030100
    │    长沙 101250100  青岛 101120200  合肥 101220100
    │
    └─ 每个城市 5 页（每页 30 条）→ brandid × 15 城市 × 5 页
       → 理论最大抓取 2250 条/品牌（实际有重复页，会自动去重）
    ↓
③ 调 PositionInfo 列表接口（CDP 前端接口）
    GET https://www.zhipin.com/wapi/zpgeek/search/joblist.json
        ?query=                    （留空或公司名）
        &scene=2                   （按公司搜职位场景）
        &companyId={brandid}
        &city={bossjob_search_city_code}
        &page={N}
        &pageSize=30
    → Response JSON.data.joblist 数组，每条是 PositionInfo
    ↓
④ PositionInfo 字段 → 原始 job 记录（直接来自 JSON，非 LLM 抽取）
    ┌────────────────────────────┬──────────────────────────────────────────────┐
    │ 输出字段                    │ JSON 路径                                    │
    ├────────────────────────────┼──────────────────────────────────────────────┤
    │ source                     │ 固定 "boss_cdp"                              │
    │ source_external_id         │ encryptJobId 或 jobId（BOSS 加密 ID）        │
    │ title                      │ jobName                                      │
    │ city                       │ jobAreaString → 与 search_city 强校验         │
    │ salary_raw                 │ jobSalaryDesc string（如 "25-50K"）          │
    │ salary_min / salary_max    │ 规则解析 salary_desc（见 2.3）                │
    │ experience                 │ jobExperienceString → 映射成枚举             │
    │ degree                     │ jobDegreeString → 映射成枚举                 │
    │ company_id                 │ 由 brandid → Company 映射得出                 │
    │ source_url                 │ 合成：/job_detail/{encryptJobId}.html         │
    │ description                │ 留空（BOSS 详情需要二次访问，这里不抓）        │
    └────────────────────────────┴──────────────────────────────────────────────┘
    ↓
⑤ normalize_job（BOSS 分支）→ 规则去重 → 入库
    注意：description 字段不填也能通过校验，去重和评分只依赖
    title / salary / city / experience / degree + source_external_id。
```

### 2.3 BOSS salary_desc 字符串解析规则（纯规则，无 LLM）

位于 `backend/app/ingestion/normalization/job.py` 的 `_parse_boss_salary()`：

| 输入示例 | 解析结果 |
|---|---|
| `"20-40K"` | min=20, max=40, unit="k_month", negotiable=False |
| `"15-25K·14薪"` | min=15, max=25, unit="k_month", bonus_months=14 |
| `"30-60万"` | min=30, max=60, unit="w_year" |
| `"面议"` | min/max=None, negotiable=True |
| `"200元/天"` | min=200, max=200, unit="yuan_day"（日结实习） |

城市校验：若 `jobAreaString` 解析出的城市和本轮 `bossjob_search_city` 不一致（常见于 BOSS 跨城市投放的总部职位）→ 标记 `city_overridden=True`，去重时仍以**搜索城市**为准，避免出现「在杭州搜出北京总部职位」的脏数据。

---

## 3 · 两条路径的差异速查表

| 维度 | ATS Provider（路径 A） | BOSS CDP Provider（路径 B） |
|---|---|---|
| 覆盖公司 | 海外大厂 + 国内用 ATS 的创新公司（greenhouse/lever/workday/ashby） | **国内所有入驻 BOSS 的公司**（覆盖面最广） |
| 职位地域 | 全球（官网公布的所有 office） | 仅大陆 15 个重点城市（枚举 city_code） |
| 数据源 | 官网自披露（准确度高） | 招聘方在 BOSS 填写的公开职位页（更新快） |
| 是否需要登录 | 否（公开 HTML / JSON） | 否（CDP wapi 公开接口即可） |
| description 字段 | 经常有（模式 2 DOM 或 LD+JSON description） | **留空**（详情页需要二次请求 + 风控，此处不抓） |
| 回退到 LLM 提取？ | ✕ 已永久删除，ATS 未命中直接返回 `ats_entry_discovery_pending` | ✕ 完全走 PositionInfo JSON 字段映射 |
| 公司-brand 映射方式 | ATS 域名/页面内品牌标识 → CompanyProfileField.ats_provider_ids | brandid → CompanyProfileField.boss_brandid |
| 抓取速度 | 慢（Playwright 渲染，约 1~2 分钟/公司） | 快（HTTP GET JSON，约 10s/品牌） |

---

## 4 · 代码入口的「已删除 LLM 分支」说明

在清理 LLM 调用之前，ATS / BOSS 路径抓不到职位或公司信息时，会回退到：
- `CrewExtractor.discover(query, documents)` → `Candidate(normalized_name, score)` → 再进入
- `CrewExtractor.extract_profile(candidate, doc_profiles)` / `CrewExtractor.extract_jobs(candidate, doc_jobs)`

以上整条链在 2026-08-12 从 [orchestrator.py](file:///d:/tools_dev/company_search/backend/app/ingestion/orchestrator.py) 中**整块删除**（约 90 行），并同步移除：
- `backend/app/ingestion/extraction/crew.py`（CrewExtractor / Extractor Protocol 实现）
- `backend/app/ingestion/extraction/prompts.py`（LLM 提示词构建）
- `backend/app/enrichment/`（官网信息增强模块，依赖 LLM）
- `rankings/cli.py --enrich` 分支
- `rankings/service.py enrich_ai_pilot()` 函数

现在 ATS / BOSS 两条路径都是**端到端结构化**，没有任何 LLM 回退兜底。
