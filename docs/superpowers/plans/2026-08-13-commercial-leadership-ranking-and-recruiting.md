# AI 公司商业领导力榜与招聘信息采集实施计划

## 目标

将现有 `ai-long-term-v2` 五维阶段百分位榜单迁移为 `ai-commercial-leadership-v1` 商业领导力榜，并将两条招聘路径统一到“具体岗位、80 分准入、30 天复验”的数据契约。

实施分为两条按顺序交付的主线：

1. 公司身份、Excel 基线、网络商业证据、选择性天眼查采集、新评分和榜单发布；
2. 职位模型升级、22 家 JobHunt-CLI 路由、非白名单知乎发现与职位解析、生命周期和前端展示。

## 依据与基线

- 已批准规格：`docs/superpowers/specs/2026-08-13-commercial-leadership-ranking-and-recruiting-design.md`
- 当前迁移头：未提交的 `0021_company_identity_anchor`。
- 当前工作区包含大量用户未提交的公司身份、排名、职位采集、测试和前端改动。实施每个任务前必须记录 `git status --short`，对重叠文件逐块保留现有变更；不得通过 checkout、reset 或覆盖文件回退用户工作。
- `0021_company_identity_anchor.py`、`identity_anchor.py` 及其测试当前未提交。Task 1 必须先验证并固定这组改动；后续迁移只有在它成为稳定基线后才能使用 `0022` 编号。
- 递归删除任何文件前必须解析绝对路径、确认目标位于 `D:\tools_dev\company_search`、确认无保留调用方，并优先使用 `apply_patch` 逐文件删除。本计划默认不需要递归删除。
- 默认测试使用 fixture、fake HTTP 和 fake subprocess；不得把真实网络、真实天眼查额度、招聘网站登录或浏览器反爬行为作为单元测试前提。

## 完成标准

### 公司榜单

- 示例 Excel 的 4,366 条数据行可以逐行解析，天眼评分不再是导入必需条件或评分输入。
- 工商曾用名与网络验证别名分开保存，未验证别名不能参与自动证据归属。
- 网络商业证据使用明确合同和证据等级，不保存为无模式自由文本。
- 天眼查默认只对有正式排名可能的公司调用中标、资质和重大风险；融资按线索调用；不全量调用专利和软件著作权。
- 新快照使用 `ai-commercial-leadership-v1`，四个分项合计 100；不执行发展阶段百分位校准。
- 缺少 AI 身份、商业规模、市场领导力或新鲜风险核验的公司进入观察池，不生成名次。
- 旧 `ai-long-term-v2` 快照保留为历史数据，但公共接口只发布当前新规则版本。

### 招聘信息

- 22 家固定白名单只使用 JobHunt-CLI 0.2.4；其他公司只使用知乎全网搜索发现路径。
- 搜索摘要、公司主页、列表页和未拆分多岗位公告不能成为职位。
- 多岗位批次拆为具体岗位；社招、校招和实习分开；相同职责的多城市岗位合并为 `locations[]`。
- 薪资未公开不扣分；无日期但仍可投递的岗位可入库。
- 只有达到 80 分且未命中硬门槛的岗位入库。
- 无日期岗位 30 天未复验停止展示；明确截止岗位按北京时间自动过期；历史记录不删除。
- 招聘数据不参与公司资格、总分或名次。

## 第一阶段：基线、身份与 Excel 导入

## Task 1：冻结当前未提交基线并验证 0021 身份锚点

### 文件

- 现有未提交：`backend/alembic/versions/0021_company_identity_anchor.py`
- 现有未提交：`backend/app/rankings/identity_anchor.py`
- 现有未提交：`backend/tests/rankings/test_identity_anchor.py`
- 修改：`backend/tests/migrations/test_migrations.py`

### 步骤

1. 保存任务开始时的 `git status --short` 和重叠文件 diff，不暂存无关用户改动。
2. 审核 0021 的 upgrade/downgrade、SQLite/PostgreSQL 类型兼容和 server default 清理策略。
3. 验证 `legal_name`、`tianyancha_company_id`、`uscc_sha256`、状态和锚定时间的最小性；统一社会信用代码只保存哈希。
4. 为迁移增加 upgrade/downgrade 测试，为身份锚定增加唯一匹配、失败转复核、重复运行幂等测试。
5. 确认 22 家 JobHunt 品牌与合法主体的映射边界；缺失映射不允许模糊匹配。

### 验证

```powershell
cd backend
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m pytest tests/rankings/test_identity_anchor.py tests/migrations/test_migrations.py -q
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic downgrade 0020_company_public_profile
.\.venv\Scripts\python.exe -m alembic upgrade head
```

## Task 2：重构 Excel 契约为全量增量基线

### 文件

- 修改：`backend/app/rankings/selection.py`
- 修改：`backend/app/rankings/service.py`
- 修改：`backend/app/rankings/cli.py`
- 修改：`backend/app/rankings/approved_baseline_cli.py`
- 修改：`backend/tests/rankings/test_selection.py`
- 修改：`backend/tests/rankings/test_service.py`
- 新增：`backend/tests/rankings/fixtures/commercial_baseline.xlsx` 或等价的测试构造器

### 步骤

1. 将 Excel 读取与“代表性抽样”解耦；生产导入读取全部有效行，不使用天眼评分分层抽样。
2. 公司名称和统一社会信用代码成为行级必需字段；天眼评分改为可选质量字段，不缺失拒绝、不参与身份哈希和评分。
3. `RankingCandidate` 补齐 Excel 的工商曾用名、英文名、登记状态和评分需要的资本/人员字段；电话、邮箱、法定代表人和完整地址不进入候选契约。
4. 单行解析失败形成稳定拒绝原因并继续处理其他行；报告成功、拒绝和原因计数。
5. 用户保证输入去重，因此本期不实现重复上传更新；若同一文件内部出现重复公司名或统一社会信用代码，整行拒绝并报告，不静默选一条。
6. 保存输入文件指纹、源行和身份哈希；不得保存统一社会信用代码原文。
7. 使用示例结构测试 4,366 行可解析的契约，同时用小 fixture 覆盖坏行。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/rankings/test_selection.py tests/rankings/test_service.py -q
```

## Task 3：持久化工商曾用名与证据化公司别名

### 文件

- 修改：`backend/app/models/company.py`
- 修改：`backend/app/models/__init__.py`
- 新增：`backend/alembic/versions/0022_company_alias_evidence.py`
- 新增：`backend/app/company_identity/aliases.py`
- 新增：`backend/app/company_identity/repository.py`
- 新增：`backend/tests/company_identity/test_aliases.py`
- 修改：`backend/tests/migrations/test_migrations.py`
- 修改：`backend/tests/rankings/test_service.py`

### 步骤

1. 先审计现有 `company_aliases` 使用方；不要直接扩展其全局 `normalized_alias` 唯一约束，除非确认不会错误阻止不同公司的候选简称。
2. 为别名保存 `alias_type`、来源、验证状态、验证时间；工商曾用名标为 Excel 基线类型，网络别名必须关联 `SourceDocument`。
3. 建立状态：候选、已验证、已拒绝；只有已验证别名进入公司名称池和公开 API。
4. 别名验证仅接受公司官网、政府、交易所或等价权威页面明确关系；搜索摘要只能创建候选，不能验证。
5. 产品、集团、母公司和兄弟公司名称默认拒绝自动等价。
6. 迁移现有别名时保留内容但标记来源/状态；不能凭已有行自动提升为网络已验证别名。
7. 为别名标准化、同公司去重、跨公司歧义、证据删除策略和公开过滤建立测试。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/company_identity/test_aliases.py tests/rankings/test_service.py tests/migrations/test_migrations.py -q
```

## 第二阶段：商业证据与选择性采集

## Task 4：定义版本化商业证据契约

### 文件

- 新增：`backend/app/rankings/commercial/__init__.py`
- 新增：`backend/app/rankings/commercial/contracts.py`
- 新增：`backend/app/rankings/commercial/evidence.py`
- 修改：`backend/app/rankings/models.py`
- 新增：`backend/alembic/versions/0023_commercial_ranking_evidence.py`
- 新增：`backend/tests/rankings/commercial/test_contracts.py`
- 新增：`backend/tests/rankings/commercial/test_evidence.py`
- 修改：`backend/tests/migrations/test_migrations.py`

### 步骤

1. 复用 `SourceDocument` 和 `CompanyRankingSignal`，不要创建平行原始响应表。
2. 定义允许的商业信号键：`verified_revenue`、`market_position`、`customer_order_proof`、`winning_bid`、`active_core_qualification`、`material_risk`、`ai_business_identity` 和可选 `financing_context`。
3. 每条信号显式保存主体、适用年度/事件日期、统计口径、来源等级、验证状态、采集和过期时间、稳定指纹。
4. 为 `market_position` 和 `customer_order_proof` 定义离散证据等级，禁止任意模型自由给分。
5. 为客户与订单主指标保存商业模式类型：项目制、SaaS、消费互联网、模型/API、硬件/机器人；同一快照只允许一个主体系计分。
6. 定义冲突状态；同字段不同主体、年度或口径不自动覆盖或平均。
7. 迁移只增加新规则需要的最小字段/约束，保留旧信号和旧快照可读。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/rankings/commercial/test_contracts.py tests/rankings/commercial/test_evidence.py tests/migrations/test_migrations.py -q
```

## Task 5：实现网络商业证据发现与验证服务

### 文件

- 新增：`backend/app/rankings/commercial/discovery.py`
- 新增：`backend/app/rankings/commercial/verification.py`
- 修改：`backend/app/ingestion/providers/zhihu.py`
- 修改：`backend/app/ingestion/providers/http.py`
- 修改：`backend/app/ingestion/providers/robots.py`
- 修改：`backend/app/core/config.py`
- 修改：`.env.example`
- 新增：`backend/tests/rankings/commercial/test_discovery.py`
- 新增：`backend/tests/rankings/commercial/test_verification.py`
- 修改：`backend/tests/ingestion/providers/test_zhihu.py`

### 步骤

1. 复用 `ZhihuGlobalSearchProvider` 作为 URL 发现边界，建立每家公司、每类证据的查询与候选预算；不使用未公开分页。
2. 查询覆盖 AI 身份、营收、市场地位、客户/用户/订单和别名；连续查询没有新增候选时停止，不强制固定四组全部执行。
3. 搜索结果只产生候选 URL。验证服务使用现有安全 HTTP、重定向复检和 robots 约束读取允许页面。
4. 按证据优先级分类交易所/审计财报、政府、官网/年报、天眼结构化记录和可信媒体转述。
5. 验证营收的主体、年度、币种和口径；集团营收、合同额、注册资本和估值不得投影为公司营收。
6. 将市场排名、用户数、ARR、客户、出货量等投影到离散契约；不允许搜索摘要或无来源模型结论直接计分。
7. 别名候选进入 Task 3 的验证流程；AI 身份只产生准入信号，不加分。
8. 网络失败记录未知/待补采，不创建零值信号。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/rankings/commercial/test_discovery.py tests/rankings/commercial/test_verification.py tests/ingestion/providers/test_zhihu.py -q
```

## Task 6：将天眼查采集改为商业缺口计划

### 文件

- 修改：`backend/app/rankings/gap_plan.py`
- 修改：`backend/app/rankings/tianyancha/client.py`
- 修改：`backend/app/rankings/tianyancha/contracts.py`
- 修改：`backend/app/rankings/tianyancha/projectors.py`
- 修改：`backend/app/rankings/tianyancha/service.py`
- 修改：`backend/app/rankings/approved_collection_cli.py`
- 修改：`backend/tests/rankings/test_gap_plan.py`
- 修改：`backend/tests/rankings/tianyancha/test_client.py`
- 修改：`backend/tests/rankings/tianyancha/test_projectors.py`
- 修改：`backend/tests/rankings/tianyancha/test_service.py`

### 步骤

1. 用商业类别替换默认四类计划：`market_validation`（中标+资质）、`material_risk`、可选 `financing_context`。
2. 专利和软件著作权保留历史解析兼容，但从新规则默认计划和调用预算中移除。
3. 只有通过 AI 身份且网络证据表明仍可能达到正式排名门槛的公司才调用中标和资质。
4. 只有满足其他正式资格前置条件的公司才调用重大风险；风险必须成功且新鲜才能正式排名。
5. 融资只有存在明确网络线索且被显式请求时调用；不进入主要四维分数，只作解释上下文。
6. 中标投影继续使用全名称池验证中标方并对重复项目去重；资质必须判断有效期与主营相关性，不能按数量直接得分。
7. 查询失败或配额耗尽保持未知并可恢复；绝不生成零事件信号。
8. 测试断言典型正式候选最多默认 3 个底层工具调用，明显证据不足公司为 0–2 次，专利/软著为 0 次。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/rankings/test_gap_plan.py tests/rankings/tianyancha -q
```

## 第三阶段：新评分、发布与公开 API

## Task 7：实现 `ai-commercial-leadership-v1` 纯评分函数

### 文件

- 新增：`backend/app/rankings/commercial/scoring.py`
- 新增：`backend/app/rankings/commercial/capital.py`
- 新增：`backend/app/rankings/commercial/eligibility.py`
- 修改：`backend/app/rankings/scoring.py`（仅保留旧规则兼容入口）
- 新增：`backend/tests/rankings/commercial/test_scoring.py`
- 新增：`backend/tests/rankings/commercial/test_capital.py`
- 新增：`backend/tests/rankings/commercial/test_eligibility.py`

### 步骤

1. 定义四项：`commercial_scale` 40、`market_leadership` 35、`operating_foundation` 15、`operating_reliability` 10。
2. 实现规格中的营收分档、参保人数百分位、市场地位等级、客户与订单等级、成立年限、资本和风险扣分。
3. 解析人民币注册/实缴资本；实缴优先、注册兜底、不叠加。外币没有可信换算日期时为未知。
4. 参保人数百分位以本次发布的全部有效成员（正式+观察）计算，并将 P25/P50/P75/P90 阈值保存进快照或规则元数据。
5. 市场地位只取同一市场最高证据；客户与订单只使用一个主商业模式体系。
6. 风险未知不赋 0 或 10；资格函数将其置为观察。
7. 资格必须满足 AI 身份、规模信号、领导力信号、两项合计至少 20、新鲜风险核验和无身份/关键证据冲突。
8. 同分排序固定为市场领导力、商业规模、可靠性、经营基础、工商全称。
9. 评分函数为纯函数，固定证据与阈值重复执行得到同一结果；不访问网络或数据库。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/rankings/commercial -q
```

## Task 8：生成新快照并保留旧规则历史

### 文件

- 修改：`backend/app/rankings/service.py`
- 修改：`backend/app/rankings/repository.py`
- 修改：`backend/app/rankings/cli.py`
- 修改：`backend/app/rankings/models.py`
- 修改：`backend/tests/rankings/test_service.py`
- 修改：`backend/tests/rankings/test_models.py`
- 新增：`backend/tests/integration/test_commercial_ranking_pipeline.py`

### 步骤

1. 新发布命令读取全量成员和经验证商业信号，计算并写 `ai-commercial-leadership-v1` 快照。
2. 旧 `ai-long-term-v2` 快照、信号和证据保持不变，可供审计但不再作为当前发布规则。
3. 快照保存四项分数、原始输入摘要、参保百分位阈值、资格原因、缺失/未知字段和证据关联。
4. 正式成员分配稳定名次；观察成员 `rank=None`。
5. 规则变化创建新快照，不更新旧版本；同规则同证据重复运行幂等。
6. 招聘计数只能在公共投影阶段附加，不进入评分输入或资格判断。
7. 集成测试覆盖 Excel → 网络 fixture → 选择性天眼 fixture → 评分 → 正式/观察发布。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/rankings/test_models.py tests/rankings/test_service.py tests/integration/test_commercial_ranking_pipeline.py -q
```

## Task 9：迁移公司榜单与详情 API

### 文件

- 修改：`backend/app/rankings/schemas.py`
- 修改：`backend/app/rankings/public_service.py`
- 修改：`backend/app/rankings/repository.py`
- 修改：`backend/app/companies/schemas.py`
- 修改：`backend/app/companies/repository.py`
- 修改：`backend/app/companies/service.py`
- 修改：`backend/tests/api/test_rankings.py`
- 修改：`backend/tests/api/test_companies.py`
- 修改：`backend/tests/companies/test_service.py`

### 步骤

1. 公共四项字段改为商业规模、市场领导力、经营基础、经营可靠性；移除旧五项输出依赖。
2. 榜单列表返回名次/观察、已验证常用别名、总分、四项分数、核心理由、行业、城市、阶段标签、高置信岗位数和更新时间。
3. 公司详情返回已核验营收/未公开、参保人数及年份、市场地位、客户/订单证明、核心资质、重大风险概要和公开证据。
4. 只投影已验证别名；候选/拒绝别名不公开。
5. 不公开统一社会信用代码、原始经营范围、法定代表人、电话、邮箱、完整地址、天眼原始响应或内部运行字段。
6. 公共服务只选择当前 `ai-commercial-leadership-v1` 发布，不因数据库仍有旧快照混用规则。
7. 观察公司返回稳定原因，可靠性未知时不得显示误导性 0 或 10。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/api/test_rankings.py tests/api/test_companies.py tests/companies/test_service.py -q
```

## 第四阶段：职位数据契约与可信度

## Task 10：升级职位模型、枚举和生命周期字段

### 文件

- 修改：`backend/app/models/job.py`
- 修改：`backend/app/models/enums.py`
- 修改：`backend/app/models/__init__.py`
- 新增：`backend/alembic/versions/0024_high_confidence_job_contract.py`
- 修改：`backend/app/job_enumeration/contracts.py`
- 修改：`backend/app/ingestion/jobs/contracts.py`
- 修改：`backend/tests/migrations/test_migrations.py`
- 修改：`backend/tests/job_enumeration/test_service.py`
- 修改：`backend/tests/ingestion/jobs/test_contracts.py`

### 步骤

1. 为职位增加 `locations`、职责、要求、学历、经验、招聘人数、截止时间、验证时间、有效性类型、投递模式、批次、附件、`confidence_score` 和公开状态所需字段。
2. 将招聘类型统一映射为社招、校招、实习；保留旧枚举值的迁移兼容并明确映射，避免破坏历史行。
3. 薪资字段保持可空；不得使用第三方画像补值。
4. `JobSource` 继续保存具体来源和申请 URL；主来源和从属来源不复制 `JobPosting`。
5. 数据库约束保证分数 0–100、合法状态和时间关系；SQLite/PostgreSQL 都能迁移。
6. 迁移历史职位时标记为需要复验；不能自动把旧职位提升为 80 分高置信。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/migrations/test_migrations.py tests/job_enumeration/test_service.py tests/ingestion/jobs/test_contracts.py -q
```

## Task 11：实现职位硬门槛和 80 分纯评分器

### 文件

- 新增：`backend/app/jobs/__init__.py`
- 新增：`backend/app/jobs/confidence.py`
- 新增：`backend/app/jobs/identity.py`
- 新增：`backend/app/jobs/contracts.py`
- 新增：`backend/tests/jobs/test_confidence.py`
- 新增：`backend/tests/jobs/test_identity.py`

### 步骤

1. 先执行硬门槛：主体不明、非具体岗位、过期/关闭、无来源、无投递方式、画像推导、批次未拆分、关联主体不明或身份冲突直接拒绝。
2. 按规格实现主体匹配 25、开放状态 30、来源权威 20、投递链路 15、完整度 10。
3. 完整度只看岗位名称、类型、地点、职责和要求；薪资、学历、经验、人数未公开不推测，薪资不扣分。
4. 官方/主流平台无发布日期但当前可投递时允许达到 80；登录后沟通是合法投递模式。
5. 只有 `score >= 80` 且硬门槛全过才返回可持久化候选。
6. 评分原因保存稳定代码，不保存任意异常或模型解释作为逻辑输入。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/jobs/test_confidence.py tests/jobs/test_identity.py -q
```

## Task 12：实现批次拆分、多城市合并与跨来源去重

### 文件

- 修改：`backend/app/ingestion/jobs/parser.py`
- 修改：`backend/app/ingestion/normalization/job.py`
- 修改：`backend/app/ingestion/deduplication/job.py`
- 修改：`backend/app/ingestion/persistence/service.py`
- 新增：`backend/app/jobs/batches.py`
- 新增：`backend/app/jobs/merge.py`
- 修改：`backend/tests/ingestion/jobs/test_parser.py`
- 修改：`backend/tests/ingestion/normalization/test_job.py`
- 修改：`backend/tests/ingestion/deduplication/test_job.py`
- 修改：`backend/tests/ingestion/persistence/test_service.py`
- 新增：`backend/tests/jobs/test_batches.py`
- 新增：`backend/tests/jobs/test_merge.py`

### 步骤

1. 将政府、国企、人才网和附件中的岗位表解析为逐岗位候选；附件无法解析时只记录采集线索，不写职位。
2. 每个岗位独立保存批次名、公告 URL、附件 URL、字段和评分。
3. 精确去重使用公司、规范化岗位名、招聘类型和稳定来源 ID；无 ID 时使用规范化内容指纹。
4. 模糊合并比较岗位名、招聘类型、职责和要求；职责/要求实质一致的多城市候选合并 `locations[]`。
5. 职级、职责或要求不同必须拆分；社招、校招和实习永远不合并。
6. 主来源优先级为官方具体职位、政府公告、官方 ATS、主流平台具体职位、其他一手来源；有效从属来源保留。
7. 持久化层拒绝未评分、低于 80 或硬门槛失败的候选，形成最后一道防线。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/jobs/test_batches.py tests/jobs/test_merge.py tests/ingestion/jobs tests/ingestion/normalization/test_job.py tests/ingestion/deduplication/test_job.py tests/ingestion/persistence/test_service.py -q
```

## 第五阶段：两条招聘路由与生命周期

## Task 13：将 22 家 JobHunt 输出接入统一高置信契约

### 文件

- 修改：`backend/data/jobhunt_sites.json`
- 修改：`backend/app/job_enumeration/jobhunt.py`
- 修改：`backend/app/job_enumeration/site_registry.py`
- 修改：`backend/app/job_enumeration/service.py`
- 修改：`backend/app/job_enumeration/persistence.py`
- 修改：`backend/app/tasks/job_enumeration.py`
- 修改：`backend/tests/job_enumeration/test_jobhunt.py`
- 修改：`backend/tests/job_enumeration/test_site_registry.py`
- 修改：`backend/tests/job_enumeration/test_service.py`
- 修改：`backend/tests/job_enumeration/test_persistence.py`

### 步骤

1. 固定并测试 22 家映射，不动态扩充；使用已验证合法主体和别名匹配，不用子串模糊匹配。
2. 保持 JobHunt-CLI 0.2.4 的绝对路径、版本固定、参数数组、超时和输出上限边界。
3. 将输出映射到 Task 10 契约，推断社招/校招/实习时保留来源依据；未知类型不能与其他类型合并。
4. 每个候选执行 Task 11 硬门槛/评分，再执行 Task 12 合并/去重；JobHunt 来源本身不自动获得准入。
5. 完整快照生命周期只作用于真正完整的 CLI 结果；低分丢弃和解析拒绝不能伪装为完整零职位。
6. CLI 失败记录稳定错误，不回退知乎或其他招聘平台。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/job_enumeration -q
```

## Task 14：实现非白名单知乎招聘发现与具体来源解析

### 文件

- 修改：`backend/app/ingestion/entry_discovery/contracts.py`
- 修改：`backend/app/ingestion/entry_discovery/service.py`
- 修改：`backend/app/ingestion/entry_verification/service.py`
- 修改：`backend/app/ingestion/orchestrator.py`
- 修改：`backend/app/ingestion/runtime.py`
- 修改：`backend/app/ingestion/production.py`
- 修改：`backend/app/tasks/collection.py`
- 新增：`backend/app/jobs/source_classifier.py`
- 新增：`backend/app/jobs/source_parser.py`
- 修改：`backend/tests/ingestion/entry_discovery/test_service.py`
- 修改：`backend/tests/ingestion/entry_verification/test_service.py`
- 修改：`backend/tests/ingestion/test_orchestrator.py`
- 新增：`backend/tests/jobs/test_source_classifier.py`
- 新增：`backend/tests/jobs/test_source_parser.py`

### 步骤

1. 白名单公司在编排入口处明确路由到 JobHunt，非白名单才允许知乎招聘发现。
2. 知乎 API 只产生候选 URL；搜索摘要、公司主页和平台列表页不能生成职位。
3. 使用名称池进行主体匹配，但只有已验证别名可自动接受；候选别名导致复核/拒绝。
4. 分类公司官网、政府/国资/人社、官方人才网、ATS、主流招聘具体页和其他一手来源。
5. 只使用现有安全 HTTP、允许的结构化 API或已批准浏览器路径；不绕过登录、验证码和反爬。
6. 将具体页面或公告附件解析为逐岗位候选，随后统一评分、合并、去重和持久化。
7. 每家公司设置搜索调用、候选 URL、HTTP 页面和附件大小预算；找到可验证入口后短路无必要查询。
8. 不能宣称全网完整覆盖；运行报告记录实际查询、候选、解析、拒绝和入库计数。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/jobs/test_source_classifier.py tests/jobs/test_source_parser.py tests/ingestion/entry_discovery tests/ingestion/entry_verification tests/ingestion/test_orchestrator.py -q
```

## Task 15：实现到期、30 天复验和公开过滤

### 文件

- 修改：`backend/app/tasks/expiration.py`
- 修改：`backend/app/tasks/schedule.py`
- 修改：`backend/app/ingestion/coverage/service.py`
- 修改：`backend/app/companies/repository.py`
- 修改：`backend/app/companies/service.py`
- 修改：`backend/tests/tasks/test_expiration.py`
- 修改：`backend/tests/tasks/test_schedule.py`
- 修改：`backend/tests/ingestion/coverage/test_service.py`
- 修改：`backend/tests/api/test_companies.py`

### 步骤

1. 明确截止日期没有时刻时，按北京时间截止日次日 00:00 转 `expired`；具体时刻按原时刻执行。
2. 页面提前关闭立即转 `closed`。
3. `evergreen` 岗位以 `verified_at + 30 days` 为公开上限，超期转 `stale` 并停止展示。
4. 复验成功更新 `verified_at` 并恢复 `active`；失败/封禁不自动判关闭，按未知和既有时效边界处理。
5. 查询 API 默认只返回 `active` 且分数至少 80 的岗位；历史状态仍可内部审计。
6. 招聘覆盖计数只统计当前公开高置信岗位，不影响评分服务。
7. 使用冻结时钟测试北京时间边界、闰日、具体截止时刻和 30 天临界值。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/tasks/test_expiration.py tests/tasks/test_schedule.py tests/ingestion/coverage/test_service.py tests/api/test_companies.py -q
```

## 第六阶段：前端、运维与整体验收

## Task 16：更新榜单、公司详情和职位 UI

### 文件

- 修改：`frontend/src/api/types.ts`
- 修改：`frontend/src/ranking/RankingListPage.tsx`
- 修改：`frontend/src/ranking/RankingListPage.test.tsx`
- 修改：`frontend/src/company/CompanyDetailPage.tsx`
- 修改：`frontend/src/company/CompanyDetailPage.test.tsx`
- 修改：`frontend/src/company/JobList.tsx`
- 修改：`frontend/src/search/CompanyResults.tsx`
- 修改：`frontend/src/styles.css`
- 修改：`frontend/tests/detail-flow.spec.ts`
- 修改：`frontend/tests/search-flow.spec.ts`

### 步骤

1. 榜单从旧五维改为四维商业领导力，清楚区分正式名次和观察状态。
2. 展示已验证别名、行业、城市、阶段标签、高置信职位数和更新时间；不展示内部身份字段。
3. 详情页用求职者语言呈现营收/未公开、组织规模、市场地位、客户/订单、核心资质和重大风险概要。
4. 可靠性未知显示“待核验”，不得显示 0 分或“无风险”。
5. 职位列表展示多地点、招聘类型、薪资未公开、截止/长期岗位、登录后沟通提示和来源。
6. 只展示当前 `active` 高置信岗位；空状态区分“暂无高置信岗位”和“数据待复验”。
7. 更新响应式布局、无障碍标签和长证据/多地点折行。

### 验证

```powershell
cd frontend
npm test -- --run
npm run build
npm run test:e2e
```

## Task 17：提供可恢复的批处理命令与运行报告

### 文件

- 修改：`backend/app/rankings/cli.py`
- 修改：`backend/app/rankings/approved_baseline_cli.py`
- 修改：`backend/app/rankings/approved_collection_cli.py`
- 新增：`backend/app/rankings/commercial/reporting.py`
- 新增：`backend/tests/rankings/commercial/test_reporting.py`
- 修改：`README.md`
- 新增：`docs/dev/commercial-leadership-operations.md`

### 步骤

1. 将运行拆为显式命令：Excel 基线导入、网络证据发现/验证、选择性天眼查、重评分/发布、招聘刷新。
2. 每个阶段保存进度并可重跑；成功且新鲜的类别跳过，失败类别恢复后续跑。
3. 报告公司行成功/拒绝、别名候选/验证、网络证据、逻辑调用/底层工具调用、正式/观察、职位候选/硬拒绝/低分/入库/过期。
4. 不在报告、数据库、命令示例或日志中保存密钥、统一社会信用代码原文、天眼原始响应或招聘平台登录态。
5. CLI 默认不访问真实网络，只有显式开关才执行对应采集阶段。
6. README 说明三类数据源职责、22 家路由、80 分门槛、30 天复验和天眼最小调用策略。

### 验证

```powershell
cd backend
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m pytest tests/rankings/commercial/test_reporting.py -q
.\.venv\Scripts\python.exe -m app.rankings.cli --help
```

## Task 18：全量回归、迁移演练和验收证据

### 文件

- 新增：`backend/tests/integration/test_commercial_ranking_and_jobs.py`
- 修改：`docs/dev/commercial-leadership-operations.md`
- 修改：`docs/product-guide.md`

### 步骤

1. 建立端到端离线 fixture：Excel → AI 身份/别名 → 网络商业证据 → 选择性天眼查 → 新榜单 → 两条招聘路由 → 80 分入库 → 生命周期 → API。
2. 断言非候选公司不调用天眼，正式候选不调用专利/软著，JobHunt 失败不回退，非白名单不调用 JobHunt。
3. 验证旧快照保留、新快照发布、观察无名次、招聘计数不改变评分。
4. 在隔离数据库演练 0021→新迁移 head 的 upgrade/downgrade；备份正式数据库后才允许生产迁移。
5. 运行完整后端测试、Ruff、mypy、前端测试、构建和 Playwright。
6. 使用示例 Excel 执行只读预检报告，确认 4,366 行解析契约；未经用户明确授权不发起全量真实网络或天眼调用。
7. 检查 `git diff --check`、`git status --short` 和每个提交的暂存集合，确保没有覆盖任务开始前的用户改动。
8. 在运维文档记录实际测试数、条件性跳过、未执行的在线 smoke 和剩余限制；不得把 fixture 测试描述为线上稳定性证明。

### 验证

```powershell
cd backend
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest tests/integration -q
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m mypy app

cd ..\frontend
npm test -- --run
npm run build
npm run test:e2e

cd ..
git diff --check
git status --short
```

## 建议提交顺序

1. `test: verify company identity anchor baseline`
2. `refactor: import full commercial ranking baseline`
3. `feat: add evidence-backed company aliases`
4. `feat: define commercial ranking evidence`
5. `feat: discover verified commercial evidence`
6. `refactor: minimize tianyancha ranking calls`
7. `feat: score commercial leadership ranking`
8. `feat: publish commercial leadership snapshots`
9. `feat: expose commercial ranking fields`
10. `feat: add high confidence job contract`
11. `feat: score and merge high confidence jobs`
12. `feat: route approved companies through jobhunt`
13. `feat: discover non-whitelist jobs with zhihu`
14. `feat: expire and reverify job listings`
15. `feat: present commercial ranking and jobs`
16. `docs: document commercial ranking operations`

每个提交只暂存对应任务的显式文件。提交前检查 `git diff --cached --name-only`；若暂存集合包含任务外文件，立即停止提交并修正暂存集合。
