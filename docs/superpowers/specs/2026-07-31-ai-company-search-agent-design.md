# AI 公司信息搜索 Agent 应用 - 设计文档

## 1. 产品目标

为中国大陆求职者提供 AI 公司信息聚合搜索服务。用户可以按条件浏览公司、按名称精确搜索，并查看公司简介、在招职位、投递链接和备案信息。

第一期以可验证的纵向能力为目标，不以数据源数量为目标：

1. 先交付可搜索的 Web 应用、稳定的数据模型和种子数据导入能力。
2. 再接入异步采集流水线，由 Provider 获取证据，LLM 只负责非确定性抽取，确定性服务负责校验、去重和入库。

## 2. 交付拆分

### 2.1 阶段一：Web 搜索基座

- FastAPI REST API。
- React + TypeScript SPA。
- SQLite 开发和测试数据库，SQLAlchemy 2.x ORM 与 Alembic 迁移。
- 公司、别名、职位、职位来源、备案和来源证据的数据模型。
- 通过受版本控制的种子数据提供可演示、可测试的搜索体验。
- 不依赖 Redis、Celery、LLM 或外部网络即可本地运行。

### 2.2 阶段二：异步采集流水线

- Redis、Celery Worker 和 Celery Beat。
- 知乎 Global Search Provider。
- 可插拔 Provider 接口及受控的公司官网 Provider。
- CrewAI 承载公司发现、公司资料抽取和职位抽取三类 LLM 任务。
- 确定性的标准化、去重、事务入库、缓存失效和任务状态管理。
- 未取得凭证或抓取授权的数据源保持关闭，不用模拟的“成功”替代真实采集。

两个阶段分别生成 implementation plan。阶段一完成后即可独立运行；阶段二依赖阶段一的数据模型和 API 契约。

## 3. 范围

### 3.1 第一期包含

- 中国大陆 AI 公司信息聚合。
- 公司名称精确搜索和名称/别名模糊搜索。
- 按行业、细分领域、融资阶段、规模和总部城市筛选。
- 公司详情、在招职位、投递链接、ICP/算法备案。
- 无账户模式下的按需采集请求和状态轮询。
- 每日定时刷新及职位过期处理。
- SQLite 开发/测试兼容；保留 PostgreSQL 方言兼容性，但不包含生产部署。

### 3.2 第一期排除

- 用户账户、收藏、订阅和个性化推荐。
- 员工评价、面试经验、薪资分析和行业报告。
- 国际化数据源。
- PostgreSQL、Redis 和 Worker 的生产集群部署。
- 绕过登录、验证码、访问控制、robots.txt 或平台服务条款的抓取。
- 企查查、天眼查、Boss 直聘、拉勾等未取得凭证或授权的数据源实现。

## 4. 技术架构

| 层级 | 技术 | 职责 |
|------|------|------|
| 前端 | React + TypeScript + Vite | 搜索、筛选、详情和采集状态界面 |
| API | Python 3.12+、FastAPI、Pydantic 2 | REST 契约、校验和依赖注入 |
| 数据访问 | SQLAlchemy 2.x、Alembic | ORM、事务和迁移 |
| 开发数据库 | SQLite | 本地开发和自动化测试 |
| 生产兼容目标 | PostgreSQL | 仅保证模型和查询可迁移，不在第一期部署 |
| 异步任务 | Celery + Redis | 按需采集、定时刷新和任务去重 |
| Agent | CrewAI | 仅编排需要 LLM 判断的发现和抽取任务 |
| LLM | OpenAI 兼容 API | 输出经过 Pydantic 校验的结构化候选数据 |
| 缓存 | Redis | 阶段二启用列表和详情缓存 |

系统边界：

```text
Browser -> FastAPI -> Query Service -> SQLAlchemy -> Database
                  \-> Collection Request Service -> Celery -> Ingestion Orchestrator
                                                       |-> Providers
                                                       |-> CrewAI extraction tasks
                                                       |-> Normalize/Deduplicate
                                                       \-> Transactional persistence
```

## 5. 核心设计原则

1. 用户搜索只查询本地数据库；外部网络调用不处于搜索请求链路中。
2. 精确搜索未命中时，API 返回空结果；前端随后提交一次按需采集请求并轮询状态。
3. LLM 输出始终是候选数据，必须经过结构校验和确定性规则后才能入库。
4. 数据来源、来源记录和规范化实体分离，保证可追溯和幂等更新。
5. 所有异步任务都以数据库中的 `collection_requests` 和 `crawl_runs` 为事实来源，Celery task id 只用于执行关联。
6. Provider 默认关闭；只有配置、凭证和合规条件满足时才启用。

## 6. 数据模型

所有时间使用 UTC 存储，API 输出 RFC 3339。UUID 在应用层生成并以数据库无关的类型映射存储。枚举在 Python 和数据库约束中使用相同的字符串值。

### 6.1 `companies`

| 字段 | 类型 | 规则 |
|------|------|------|
| `id` | UUID | 主键 |
| `canonical_name` | VARCHAR(255) | 展示名称 |
| `normalized_name` | VARCHAR(255) | 规范化名称，唯一索引 |
| `industry` | VARCHAR(100) | 可空，筛选字段 |
| `sub_industry` | VARCHAR(100) | 可空，筛选字段 |
| `funding_stage` | VARCHAR(50) | `seed`/`angel`/`pre_a`/`series_a`/`series_b`/`series_c_plus`/`public`/`unfunded`/`unknown` |
| `scale` | VARCHAR(50) | `one_to_49`/`50_to_199`/`200_to_499`/`500_plus`/`unknown` |
| `city` | VARCHAR(50) | 可空，规范化城市名 |
| `logo_url` | VARCHAR(1000) | 可空，仅允许 HTTP(S) |
| `website` | VARCHAR(1000) | 可空，仅允许 HTTP(S) |
| `description` | TEXT | 可空，纯文本摘要 |
| `created_at` | DATETIME | 创建时间 |
| `updated_at` | DATETIME | 实体最后变更时间 |
| `last_collected_at` | DATETIME | 最近成功采集时间，可空 |

### 6.2 `company_aliases`

| 字段 | 类型 | 规则 |
|------|------|------|
| `id` | UUID | 主键 |
| `company_id` | UUID | 外键，删除公司时级联 |
| `alias` | VARCHAR(255) | 展示别名 |
| `normalized_alias` | VARCHAR(255) | 唯一索引，指向唯一公司 |

### 6.3 `source_documents`

保存采集证据的元数据和可审计摘要，不无限期保存完整第三方页面。

| 字段 | 类型 | 规则 |
|------|------|------|
| `id` | UUID | 主键 |
| `provider` | VARCHAR(50) | Provider 标识 |
| `external_id` | VARCHAR(255) | 来源内容 ID，可空 |
| `url` | VARCHAR(2000) | 规范化来源 URL |
| `title` | VARCHAR(500) | 来源标题，可空 |
| `text_excerpt` | TEXT | 用于审计的截断纯文本 |
| `content_hash` | VARCHAR(64) | SHA-256 |
| `authority_level` | SMALLINT | 1-4，可空 |
| `published_at` | DATETIME | 可空 |
| `fetched_at` | DATETIME | 获取时间 |

唯一约束为 `(provider, external_id)`；`external_id` 为空时使用 `(provider, url, content_hash)` 做应用层幂等判断。

### 6.4 `company_sources`

| 字段 | 类型 | 规则 |
|------|------|------|
| `company_id` | UUID | 联合主键、外键 |
| `source_document_id` | UUID | 联合主键、外键 |
| `covered_fields` | JSON | 该证据支持的公司字段名数组 |
| `confidence` | NUMERIC(4,3) | 0-1 |

### 6.5 `job_postings`

表示跨来源合并后的规范化职位。

| 字段 | 类型 | 规则 |
|------|------|------|
| `id` | UUID | 主键 |
| `company_id` | UUID | 外键，删除公司时级联 |
| `title` | VARCHAR(255) | 规范化职位名 |
| `normalized_title` | VARCHAR(255) | 去重字段 |
| `job_type` | VARCHAR(50) | `full_time`/`internship`/`campus`/`experienced`/`unknown` |
| `city` | VARCHAR(50) | 规范化工作城市 |
| `salary_min_monthly` | INTEGER | 人民币月薪下界，可空 |
| `salary_max_monthly` | INTEGER | 人民币月薪上界，可空 |
| `salary_months` | SMALLINT | 年薪折算月数，可空 |
| `description` | TEXT | 最完整的纯文本描述 |
| `posted_at` | DATE | 所有来源中最早发布日期，可空 |
| `is_active` | BOOLEAN | 任一未过期来源有效时为 true |
| `created_at` | DATETIME | 创建时间 |
| `updated_at` | DATETIME | 更新时间 |

候选集合只在同一公司内比较。`normalized_title` 相似度大于 85、城市一致且职位类型兼容时自动合并；边界样本才交给 LLM 判断。

### 6.6 `job_sources`

| 字段 | 类型 | 规则 |
|------|------|------|
| `id` | UUID | 主键 |
| `job_posting_id` | UUID | 外键，删除职位时级联 |
| `source_document_id` | UUID | 外键，可空 |
| `provider` | VARCHAR(50) | 来源平台 |
| `source_raw_id` | VARCHAR(255) | 平台原始 ID |
| `apply_url` | VARCHAR(2000) | 具体投递链接 |
| `first_seen_at` | DATETIME | 首次发现时间 |
| `last_seen_at` | DATETIME | 最近确认存在时间 |
| `is_active` | BOOLEAN | 来源是否仍有效 |

唯一约束为 `(provider, source_raw_id)`。同一规范化职位可以拥有多个 `job_sources`，因此来源平台、原始 ID 和投递链接不会失去对应关系。

### 6.7 `regulatory_filings`

| 字段 | 类型 | 规则 |
|------|------|------|
| `id` | UUID | 主键 |
| `company_id` | UUID | 外键，删除公司时级联 |
| `source_document_id` | UUID | 外键，可空 |
| `filing_type` | VARCHAR(50) | `icp`/`algorithm`/`business_license` |
| `filing_number` | VARCHAR(255) | 备案号 |
| `filing_name` | VARCHAR(255) | 备案名称 |
| `filing_authority` | VARCHAR(255) | 可空 |
| `filing_date` | DATE | 可空 |
| `filing_status` | VARCHAR(50) | 可空 |
| `detail_url` | VARCHAR(2000) | 可空 |
| `created_at` | DATETIME | 创建时间 |
| `updated_at` | DATETIME | 更新时间 |

唯一约束为 `(filing_type, filing_number)`。

### 6.8 `collection_requests`

表示用户可见的按需采集请求。

| 字段 | 类型 | 规则 |
|------|------|------|
| `id` | UUID | 主键 |
| `query` | VARCHAR(255) | 用户原始查询 |
| `normalized_query` | VARCHAR(255) | 任务去重键 |
| `status` | VARCHAR(20) | `queued`/`running`/`succeeded`/`partial`/`failed` |
| `company_id` | UUID | 成功匹配或创建的公司，可空 |
| `error_code` | VARCHAR(50) | 可公开的稳定错误码，可空 |
| `created_at` | DATETIME | 创建时间 |
| `updated_at` | DATETIME | 更新时间 |
| `completed_at` | DATETIME | 可空 |

同一 `normalized_query` 存在 `queued` 或 `running` 请求时，POST 返回现有请求，不重复入队。

### 6.9 `crawl_runs`

表示一次后台执行及其可观测结果。

| 字段 | 类型 | 规则 |
|------|------|------|
| `id` | UUID | 主键 |
| `collection_request_id` | UUID | 外键，可空 |
| `company_id` | UUID | 外键，可空 |
| `run_type` | VARCHAR(30) | `discovery`/`company_refresh`/`on_demand`/`expiration` |
| `status` | VARCHAR(20) | 与任务状态枚举一致 |
| `celery_task_id` | VARCHAR(255) | 可空，不作为事实状态 |
| `providers_attempted` | JSON | Provider 标识数组 |
| `documents_found` | INTEGER | 默认 0 |
| `jobs_found` | INTEGER | 默认 0 |
| `jobs_written` | INTEGER | 默认 0 |
| `error_code` | VARCHAR(50) | 可空 |
| `error_detail` | TEXT | 仅后台可见，截断且不含密钥 |
| `claim_token` | VARCHAR(36) | 可空，UUID Worker 代际令牌 |
| `started_at` | DATETIME | 可空 |
| `completed_at` | DATETIME | 可空 |
| `created_at` | DATETIME | 创建时间 |

## 7. REST API 契约

API 前缀固定为 `/api/v1`，JSON 字段使用 `snake_case`。

### 7.1 公司列表与搜索

`GET /api/v1/companies`

参数：

- `q`：可选，匹配规范化公司名和别名。
- `industry`、`sub_industry`、`funding_stage`、`scale`、`city`：可选精确筛选。
- `page`：默认 1，最小 1。
- `page_size`：默认 20，范围 1-100。
- `sort`：`relevance`、`name`、`updated_at`；无 `q` 时默认 `updated_at`，有 `q` 时默认 `relevance`。

响应：

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": 0
}
```

精确命中规则按 `normalized_name`、`normalized_alias` 顺序执行，随后才执行包含匹配。空结果返回 `200` 和空数组。

### 7.2 公司详情

- `GET /api/v1/companies/{company_id}`：返回公司字段、别名、备案、来源摘要和职位数量。
- `GET /api/v1/companies/{company_id}/jobs`：支持 `job_type`、`city`、`active_only`、`page`、`page_size`；每个职位返回来源及投递链接。
- UUID 格式错误返回 `422`，实体不存在返回 `404`。

### 7.3 按需采集

`POST /api/v1/collection-requests`

请求：

```json
{ "query": "示例公司" }
```

查询长度为 2-100 个规范化后的字符。新请求返回 `202`；相同活动请求也返回 `202` 和同一个请求 ID。

`GET /api/v1/collection-requests/{request_id}` 返回状态、公开错误码及成功后的 `company_id`。前端对 `queued` 和 `running` 使用 2 秒起步、最大 10 秒的退避轮询，并在 2 分钟后停止自动轮询但保留手动刷新。

阶段一未启用异步采集时，POST 返回 `503` 和错误码 `collection_unavailable`；搜索和浏览功能仍完整可用。

### 7.4 错误响应

```json
{
  "error": {
    "code": "stable_machine_code",
    "message": "面向用户的简短说明"
  }
}
```

响应和日志不得包含第三方密钥、LLM 提示词、原始堆栈或未经清理的页面内容。

## 8. 前端体验

第一屏就是搜索工作区，不创建营销落地页。

- 顶部固定搜索框，主区域提供紧凑的筛选栏和公司结果列表。
- URL 查询参数保存搜索词、筛选条件、页码和排序，刷新与前进后退可恢复状态。
- 公司详情页展示公司信息、备案和职位列表；投递链接明确标识来源平台并在新标签页打开。
- 搜索无结果时自动尝试提交一次采集请求。服务不可用时显示稳定的空状态，不循环重试。
- 采集状态显示 `排队中`、`采集中`、`部分完成`、`已完成`、`失败`，成功后跳转到公司详情。
- 所有交互支持键盘，加载、空数据、错误和窄屏状态都有测试覆盖。

## 9. 采集架构

### 9.1 Provider 接口

Provider 是普通 Python 类，不是 Agent。统一接口：

```python
class Provider(Protocol):
    name: str

    async def search(self, query: ProviderQuery) -> list[RawDocument]:
        raise NotImplementedError
```

`ProviderQuery` 包含查询词、允许域名、发布时间下界和最大结果数；`RawDocument` 包含 provider、external_id、URL、标题、纯文本、发布时间和权威等级。Provider 必须实现超时、响应大小上限和稳定错误映射。

### 9.2 知乎 Global Search Provider

- `GET https://developer.zhihu.com/api/v1/content/global_search`。
- Bearer Token 与 `X-Request-Timestamp` 认证。
- `Count` 最大 20；`Filter` 仅生成受支持的 `host` 和 `publish_time` 表达式。
- `host=="zhihu.com"` 不发送到该接口。
- 响应中的 `<em>` 高亮标签在入库前移除。
- `AuthorityLevel` 解析为 1-4 的整数。
- 当前 API 资料未提供翻页游标，因此第一期每个查询最多处理一次响应中的 20 条结果；`HasMore=true` 只记录指标，不伪造翻页。
- 连接超时 5 秒、总超时 15 秒；429 和 5xx 最多重试 3 次，使用带抖动的指数退避；其他 4xx 不重试。

### 9.3 公司官网 Provider

- 只访问已入库并通过校验的公司官网域名。
- 仅允许 HTTP(S)，禁止环回、私网、链路本地地址和重定向到非允许域名，防止 SSRF。
- 遵守 robots.txt 和站点服务条款；不处理登录、验证码或反自动化绕过。
- 单响应正文上限 2 MiB，只保留清理后的文本和审计摘要。

### 9.4 Agent 与确定性服务

CrewAI 只承载三个有明确结构化输出的角色：

1. `CompanyDiscoveryAgent`：从 `RawDocument` 提取公司候选名称和证据引用。
2. `CompanyProfileAgent`：提取公司字段、备案候选及字段置信度。
3. `JobExtractionAgent`：提取职位候选和来源字段。

以下能力必须是确定性服务：

- 名称、URL、城市、职位类型和薪资标准化。
- Pydantic schema 校验及不可信文本清理。
- 来源 ID 精确去重和 rapidfuzz 相似度候选选择。
- 数据库查询、唯一约束处理、事务 upsert。
- 任务状态转换、错误映射和缓存失效。

LLM 调用失败、超时或输出无效时，保留已获取的来源证据，将运行标记为 `partial` 或 `failed`，不写入未经验证的实体。

## 10. 调度、幂等与过期

### 10.1 每日刷新

每天北京时间 02:00 创建刷新任务：

```text
选择 last_collected_at IS NULL
   OR last_collected_at < now() - 24 hours 的公司
按 company_id 生成幂等任务
执行 Provider -> extraction -> normalize -> deduplicate -> transaction upsert
```

同一公司已有 `queued` 或 `running` 刷新任务时跳过新任务。

### 10.2 职位过期

- 一次采集未发现旧职位不立即下线，避免 Provider 临时缺失导致抖动。
- `job_sources.last_seen_at < now() - 30 days` 时将该来源标记为无效。
- 当职位所有来源均无效时，将 `job_postings.is_active` 设为 false。

### 10.3 状态转换

允许的状态转换：

```text
queued -> running -> succeeded
                  -> partial
                  -> failed
queued -----------> failed  # 仅限 Worker 启动前的派发失败
```

Worker 重试继续使用同一个 `crawl_run`。Worker 启动前若派发失败，允许以 `collection_unavailable` 将关联的 `collection_request` 和 `crawl_run` 从 `queued` 直接标记为 `failed`，避免留下无法发现的排队记录。持久化事务提交后才可设置 `succeeded`；任务重复投递必须返回相同最终结果，不重复创建来源记录。

## 11. 缓存

阶段一使用进程内禁用状态，不要求 Redis。阶段二启用 Redis：

- 公司列表缓存键包含完整规范化查询参数，TTL 60 秒。
- 公司详情和职位列表 TTL 300 秒。
- 公司、职位或备案事务提交后，删除对应详情键并递增全局列表版本号。
- Redis 不可用时回退数据库查询，不能导致 API 失败。

## 12. 配置、安全与合规

- 配置从环境变量读取，提供 `.env.example`，真实密钥不提交到仓库。
- 必填密钥只在对应 Provider 启用时校验。
- 外部 URL 执行 scheme、DNS/IP、重定向和响应大小校验。
- HTML 转纯文本后再送入 LLM；第三方文本按不可信数据处理，不允许其改变系统指令或工具权限。
- 日志记录 provider、run id、耗时、结果数和稳定错误码，不记录 Bearer Token 或完整第三方正文。
- 每个 Provider 有独立并发上限和速率限制。

## 13. 可观测性与错误分类

稳定错误码至少包括：

- `provider_auth_failed`
- `provider_rate_limited`
- `provider_timeout`
- `provider_invalid_response`
- `extraction_failed`
- `validation_failed`
- `persistence_conflict`
- `collection_unavailable`

API 日志包含 request id；异步日志同时包含 collection request id 和 crawl run id。`partial` 必须说明哪些 Provider 或抽取阶段失败。

## 14. 验收标准

### 14.1 阶段一

- 一条命令启动后端，一条命令启动前端；不需要外部服务。
- Alembic 可以从空 SQLite 数据库升级到最新版本。
- 种子数据重复导入不会创建重复公司、别名、职位来源或备案。
- 公司列表支持所有规定筛选、分页和排序；名称和别名均可命中。
- 公司详情展示来源、备案、职位和正确的平台投递链接。
- 以 10,000 家公司、100,000 个职位的生成数据测试，数据库查询 API 在本机测试环境下 p95 不高于 300 ms；该指标不含浏览器渲染和外部网络。
- 后端单元/集成测试及前端组件/端到端核心流程测试通过。

### 14.2 阶段二

- 使用 HTTP mock 验证知乎请求编码、认证、超时、重试、错误映射和最多 20 条结果限制。
- 同一来源响应重复处理两次，数据库实体数量不增加，`last_seen_at` 正确更新。
- 同一按需查询并发提交只产生一个活动请求和一个运行任务。
- LLM 无效 JSON、Provider 429、超时及部分来源失败都有确定的终态和可公开错误码。
- 职位来源超过 30 天未出现后失效，仍有其他有效来源的职位保持有效。
- Redis、LLM 或单个 Provider 不可用时，搜索 API 仍可读取已有数据库数据。

## 15. 项目结构

```text
company_search/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── companies/
│   │   ├── collection/
│   │   ├── ingestion/
│   │   │   ├── providers/
│   │   │   ├── extraction/
│   │   │   ├── normalization/
│   │   │   └── persistence/
│   │   ├── models/
│   │   └── tasks/
│   ├── alembic/
│   ├── tests/
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   └── tests/
└── docs/
    └── superpowers/
        ├── specs/
        └── plans/
```

代码按业务能力组织。Provider、抽取、标准化和持久化之间只通过已声明的 Pydantic 类型通信，避免 CrewAI 对数据库和 HTTP 路由产生隐式依赖。

## 16. 后续扩展

新增国家或平台时实现新的 Provider，并声明凭证、合规条件、速率限制和输出能力。LinkedIn、Wellfound、Crunchbase、Glassdoor、企查查、天眼查、Boss 直聘和拉勾均不属于第一期已启用 Provider。

## 17. Final-review contract amendments (2026-08-04)

- Production collection uses the checked-in settings-backed composition by default. `COLLECTION_RUNTIME_FACTORY` is only an optional override. Runtime composition fails before any external call unless the OpenAI-compatible endpoint, model, key, and at least one authorized Provider are configured.
- The Extractor retains exactly three methods. `extract_profile(...)` returns bounded `ProfileExtraction(profile, filings)`, and evidence-validated filings flow into `NormalizedBatch.filings`.
- Every `JobCandidate` carries the target `company_name`. Discovery accepts only a deterministic normalized name, declared alias, or normalized containment relationship to the submitted query; a sole unrelated candidate is rejected.
- `RawDocument` and all persistence DTO URLs must be public HTTP(S) URLs without credentials. Reconstructed Pydantic objects are revalidated rather than updated through unchecked copies.
- Workers atomically compare-and-set `queued -> running` and assign a fresh UUID `claim_token`; `started_at` is only the stale-time signal. A delivery that observes `running` is an in-progress no-op. Retry, reconciliation, terminal writes, and the persistence transaction require the original token; persistence locks the paired run/request ownership through commit. Claim-error recovery first rolls back the failed transaction. Dispatch failure updates only paired `queued` rows, and Beat recovers stale queued/running rows every minute.
- The OpenAI-compatible client reads streamed identity responses up to a fixed byte cap and rejects compressed or oversized responses. Extraction prompts define role-specific root arrays, required/optional fields, and supported enums before Pydantic validates the returned JSON.
- Enabled Providers share per-process concurrency and start-rate gates. Stable Zhihu codes are `provider_auth_failed` for 401/403 and `provider_rate_limited` for exhausted 429; sanitized `stage`, `code`, and optional `provider` diagnostics are persisted in `crawl_runs.error_detail`.
- Job persistence preserves the deduplicator's identity decision, never degrades a known job type to `unknown`, and rejects incompatible race winners. The incoming semantic comparison operand has no persisted posting identity.
- Company fuzzy matching uses RapidFuzz ratio semantics with the inclusive 80 threshold declared by the implementation plan.
