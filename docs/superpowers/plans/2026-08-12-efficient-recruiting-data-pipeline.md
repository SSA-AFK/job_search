# 高效招聘数据流水线实施计划

## 目标

将当前“多 Provider 发现 + 逐 ATS 搜索 + 平台渲染/解析 + LLM 职位抽取”的公司采集流程替换为两个独立阶段：

1. 低成本招聘入口 URL 验证；
2. 数据库优先、JobHunt-CLI 单源自动枚举、BOSS 人工批量补充。

实施必须保持现有公司和职位查询可用，不删除历史职位；旧 ATS 代码只在新链路验证完成且确认无调用方后移除。

## 基线与约束

- 依据规格：
  - `docs/superpowers/specs/2026-08-12-recruiting-entry-url-verification-design.md`
  - `docs/superpowers/specs/2026-08-12-external-job-enumeration-design.md`
- 工作区当前存在用户未提交的 ATS、字节跳动、排名等改动。实施前必须记录 `git status --short`；修改重叠文件时逐块保留用户变更。
- 不运行真实网络、真实 BOSS 登录、Chrome 或 npm 在线安装作为默认测试。
- JobHunt-CLI 必须固定版本，通过外部进程调用；许可证文件未补齐前不得复制、打包或分发其源码。
- BOSS 只能由显式 CLI 命令人工导入，不注册 Celery task、API 自动回退或定时任务。
- 递归删除前必须解析并核对绝对目标路径位于 `D:\tools_dev\company_search`，确认没有保留调用方，再逐文件删除。

## 完成标准

- 已有新鲜职位的公司不产生外部调用。
- 入口验证每家公司至多 1 次搜索、3 个候选、5 次 HTTP 请求，成功后立即短路。
- 自动职位枚举每家公司最多调用一个 JobHunt-CLI 进程。
- JobHunt-CLI 不支持或失败时不调用 BOSS。
- BOSS 只能人工批量导入，且永不写完整快照或关闭旧职位。
- 生产组合不再注册旧 ATS 列表 Provider、BOSS CDP Provider或职位 LLM 抽取路径。
- 后端测试、Ruff 和 mypy 通过；聚焦测试证明没有外部网络或浏览器调用。

## Task 1：冻结当前行为并建立失败测试

### 文件

- 修改：`backend/tests/ingestion/test_orchestrator.py`
- 修改：`backend/tests/ingestion/test_runtime.py`
- 新增：`backend/tests/ingestion/entry_verification/__init__.py`
- 新增：`backend/tests/ingestion/entry_verification/test_contracts.py`
- 新增：`backend/tests/ingestion/entry_verification/test_service.py`

### 步骤

1. 记录工作区状态和当前聚焦测试结果，不暂存用户已有变更。
2. 新增失败测试，描述目标编排：已有入口成功后不搜索；官网成功后不搜索；搜索最多一次；候选和 HTTP 预算耗尽即停止。
3. 新增失败测试，证明入口阶段不调用职位解析器、`DirectAtsPersistence` 或职位覆盖服务，并始终报告 `jobs_found=0`、`jobs_written=0`。
4. 将现有逐 ATS 查询和 ATS 直写测试改为“旧行为不得发生”的断言；不要先删除实现。

### 验证

```powershell
cd backend
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m pytest tests/ingestion/entry_verification tests/ingestion/test_orchestrator.py tests/ingestion/test_runtime.py -q
```

预期：新增目标测试失败，既有无关测试继续通过。

## Task 2：定义入口验证契约和预算

### 文件

- 新增：`backend/app/ingestion/entry_verification/__init__.py`
- 新增：`backend/app/ingestion/entry_verification/contracts.py`
- 修改：`backend/app/core/config.py`
- 修改：`.env.example`
- 修改：`backend/tests/core/test_config.py`
- 修改：`backend/tests/ingestion/entry_verification/test_contracts.py`

### 步骤

1. 定义 `EntryVerificationStatus`：`verified`、`unverified`、`unavailable`。
2. 定义不可变 `EntryVerificationResult`，包含候选 URL、最终 URL、状态、稳定原因、HTTP 请求数和归属证据类型。
3. 定义 `EntryVerificationBudget`，默认 `search_calls=1`、`candidates=3`、`http_requests=5`；构造时拒绝负数或零候选预算。
4. 配置仅增加三个入口预算值和请求超时；不增加平台级配置。
5. 为稳定失败代码建立显式集合，拒绝任意底层异常字符串。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/core/test_config.py tests/ingestion/entry_verification/test_contracts.py -q
```

## Task 3：实现通用 URL 验证器

### 文件

- 新增：`backend/app/ingestion/entry_verification/validator.py`
- 修改：`backend/app/ingestion/providers/http.py`
- 修改：`backend/app/ingestion/providers/robots.py`
- 新增：`backend/tests/ingestion/entry_verification/test_validator.py`
- 修改：`backend/tests/ingestion/providers/test_http.py`
- 修改：`backend/tests/ingestion/providers/test_company_site.py`

### 步骤

1. 在 `SafeHttpClient` 上暴露每次安全请求和重定向所消耗的预算信息，不建立第二套 HTTP 客户端。
2. 使用现有 DNS 固定、公网 IP、允许域和重定向复检；robots 请求同样计入预算。
3. 实现通用页面分类：成功页面、登录、验证码/封禁、404/错误页。
4. 实现招聘语义评分，只使用 URL 路径、标题、正文关键词和公开招聘链接，不解析职位卡片。
5. 实现公司归属证据：官网同域、当前运行内官网直链、可信既有绑定、规范名或确认别名。
6. 验证器不得导入 Playwright、ATS extractor、职位 parser 或 LLM client。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ingestion/entry_verification/test_validator.py tests/ingestion/providers/test_http.py tests/ingestion/providers/test_company_site.py -q
```

## Task 4：实现候选短路服务与入口持久化

### 文件

- 新增：`backend/app/ingestion/entry_verification/service.py`
- 新增：`backend/app/ingestion/entry_verification/repository.py`
- 修改：`backend/app/models/job_entry.py`
- 新增：`backend/alembic/versions/0019_entry_verification_state.py`
- 修改：`backend/app/models/enums.py`
- 修改：`backend/app/models/__init__.py`
- 修改：`backend/tests/models/test_job_entry.py`
- 新增：`backend/tests/migrations/test_entry_verification_state.py`
- 修改：`backend/tests/ingestion/entry_verification/test_service.py`

### 步骤

1. 先检查 `JobEntry` 现有字段能否表达三态和稳定原因；只为缺失的 `verification_error_code`、最终 URL/来源证据等最小字段新增迁移，避免创建平行入口表。
2. 候选顺序固定为：已有入口、官网直链、一次通用搜索。
3. 按规范化 URL 去重；第一个 `verified` 结果保存为 primary 并立即返回。
4. 每处理一个候选原子更新检查时间、状态、失败计数和原因。
5. 预算耗尽返回 `budget_exhausted`，不得调用浏览器或增加 Provider。
6. 迁移必须支持 SQLite 和 PostgreSQL upgrade/downgrade，且不改变历史职位行。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/models/test_job_entry.py tests/migrations/test_entry_verification_state.py tests/ingestion/entry_verification -q
```

## Task 5：替换入口阶段编排并断开职位抓取

### 文件

- 修改：`backend/app/ingestion/orchestrator.py`
- 修改：`backend/app/ingestion/runtime.py`
- 修改：`backend/app/ingestion/production.py`
- 修改：`backend/app/tasks/collection.py`
- 修改：`backend/tests/ingestion/test_orchestrator.py`
- 修改：`backend/tests/ingestion/test_orchestrator_builder.py`
- 修改：`backend/tests/ingestion/test_runtime.py`
- 修改：`backend/tests/tasks/test_collection_task.py`

### 步骤

1. 将公司资料采集与招聘入口验证明确分支；本计划范围内的职位入口运行只调用 `EntryVerificationService`。
2. 移除 `_ATS_DISCOVERY_TERMS`、逐平台查询、ATS URL 扫描、`DirectAtsPersistence` 和 LLM 职位抽取在该分支的调用。
3. 入口 `verified` 时 run 为 `succeeded`；正常耗尽为 `partial`；基础设施失败沿用现有可重试语义。
4. 所有入口结束路径将 `jobs_found`、`jobs_written` 固定为 0，且不创建职位快照。
5. 生产组合只保留入口需要的官网/单一搜索组件，不构造 ATS Provider 或 BOSS CDP Provider。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ingestion/test_orchestrator.py tests/ingestion/test_orchestrator_builder.py tests/ingestion/test_runtime.py tests/tasks/test_collection_task.py -q
```

## Task 6：建立职位新鲜度和单源调度边界

### 文件

- 新增：`backend/app/job_enumeration/__init__.py`
- 新增：`backend/app/job_enumeration/contracts.py`
- 新增：`backend/app/job_enumeration/service.py`
- 新增：`backend/app/job_enumeration/repository.py`
- 修改：`backend/app/core/config.py`
- 修改：`.env.example`
- 新增：`backend/tests/job_enumeration/test_service.py`
- 新增：`backend/tests/job_enumeration/test_repository.py`

### 步骤

1. 定义统一外部候选 `ExternalJobCandidate` 和枚举结果状态。
2. 用最近的完整成功快照判断 24 小时新鲜度；新鲜时返回 `fresh_database_hit`。
3. 定义公司到 JobHunt 站点键的唯一映射接口；无映射返回 `source_unsupported`。
4. 复用 collection request 或新增最小唯一约束，防止同一公司并发重复枚举；不要在内存锁上建立正确性。
5. 服务契约中不存在 BOSS 自动回退接口。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/job_enumeration/test_service.py tests/job_enumeration/test_repository.py -q
```

## Task 7：实现 JobHunt-CLI 外部进程适配器

### 文件

- 新增：`backend/app/job_enumeration/jobhunt.py`
- 新增：`backend/app/job_enumeration/site_registry.py`
- 新增：`backend/data/jobhunt_sites.json`
- 新增：`backend/tests/job_enumeration/test_jobhunt.py`
- 新增：`backend/tests/job_enumeration/fixtures/sites.json`
- 新增：`backend/tests/job_enumeration/fixtures/search_success.json`

### 步骤

1. 适配器使用参数数组启动固定可执行文件，禁止 shell 字符串拼接。
2. 启动前检查固定版本；拒绝版本漂移、缺失可执行文件和隐式在线安装。
3. 解析 `sites` JSON，加载已审核的公司 ID/别名到站点键映射；歧义停止。
4. 对已声明支持的招聘类型执行有界查询，设置超时、stdout/stderr 大小和最大记录数。
5. 校验标准字段和 URL；拒绝比例超过 20% 时整批失败。
6. 用 fixture 和 fake subprocess 测试，不调用真实 npm 或招聘网站。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/job_enumeration/test_jobhunt.py -q
```

## Task 8：接入统一职位持久化与完整快照

### 文件

- 新增：`backend/app/job_enumeration/persistence.py`
- 修改：`backend/app/ingestion/coverage/contracts.py`
- 修改：`backend/app/ingestion/coverage/service.py`
- 修改：`backend/app/ingestion/persistence/service.py`
- 新增：`backend/tests/job_enumeration/test_persistence.py`
- 修改：`backend/tests/ingestion/coverage/test_service.py`
- 修改：`backend/tests/integration/test_job_coverage_lifecycle.py`

### 步骤

1. 将 JobHunt 输出转换为现有规范化职位和 `JobSource`，来源键采用 `jobhunt:<site>` + 稳定职位 ID。
2. 复用现有来源唯一约束和公司所有权校验，禁止跨公司重挂来源。
3. 只有命令、所有招聘类型、分页和输出校验均完整时写 `succeeded + pagination_complete`。
4. 超时、截断、限流、拒绝比例超限写 `partial` 或 `failed`，不能关闭旧职位。
5. 完整零结果才允许 `empty_confirmed`。
6. 用两个连续完整快照测试职位关闭，用部分结果测试不关闭。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/job_enumeration/test_persistence.py tests/ingestion/coverage/test_service.py tests/integration/test_job_coverage_lifecycle.py -q
```

## Task 9：提供 JobHunt 自动更新任务

### 文件

- 新增：`backend/app/tasks/job_enumeration.py`
- 修改：`backend/app/tasks/celery_app.py`
- 修改：`backend/app/collection/service.py`
- 修改：`backend/app/collection/schemas.py`
- 修改：`backend/app/companies/service.py`
- 新增：`backend/tests/tasks/test_job_enumeration_task.py`
- 修改：`backend/tests/api/test_collection_requests.py`
- 修改：`backend/tests/companies/test_service.py`

### 步骤

1. 将职位更新作为独立 task 注册；入口验证 task 不直接执行 JobHunt。
2. 公司详情读取新鲜职位时不调度；缺失/过期且站点映射存在时才允许明确的更新请求。
3. 保持查询 API 只读：更新失败时仍返回数据库中的历史职位和新鲜度状态。
4. 同公司进行中的任务去重。
5. 任务结果公开稳定状态和错误码，不暴露子进程 stderr。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/tasks/test_job_enumeration_task.py tests/api/test_collection_requests.py tests/companies/test_service.py -q
```

## Task 10：实现 BOSS 人工批量导入

### 文件

- 新增：`backend/app/imports/boss_json.py`
- 新增：`backend/app/imports/boss_cli.py`
- 新增：`backend/app/job_enumeration/manual_batch.py`
- 新增：`backend/alembic/versions/0020_manual_job_import_batches.py`
- 修改：`backend/app/models/import_batch.py`
- 修改：`backend/app/models/__init__.py`
- 新增：`backend/tests/imports/test_boss_json.py`
- 新增：`backend/tests/imports/test_boss_cli.py`
- 新增：`backend/tests/job_enumeration/test_manual_batch.py`
- 新增：`backend/tests/migrations/test_manual_job_import_batches.py`

### 步骤

1. 扩展现有 import batch 模型保存工具版本、查询条件、文件指纹和计数；不保存 Chrome cookie 或登录态。
2. CLI 只读取用户明确指定的本地 JSON 文件，不启动 Chrome、不调用 scraper、不注册 API 路由或 Celery task。
3. 按品牌 ID、精确规范名或确认别名匹配；歧义和模糊匹配进入复核。
4. 文件指纹和来源 ID保证重复导入幂等。
5. BOSS 来源只新增/刷新观察到的职位，永远不调用完整快照生命周期。
6. 登录/风控错误文件作为失败批次处理，不自动重试。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/imports/test_boss_json.py tests/imports/test_boss_cli.py tests/job_enumeration/test_manual_batch.py tests/migrations/test_manual_job_import_batches.py -q
```

## Task 11：安全断开并清理旧职位采集路径

### 候选文件

- `backend/app/ingestion/providers/ats.py`
- `backend/app/ingestion/providers/ats_renderer.py`
- `backend/app/ingestion/providers/ats_extractors/`
- `backend/app/ingestion/providers/zhipin_cdp.py`
- `backend/app/ingestion/jobs/`
- `backend/app/ingestion/direct_ats.py`
- 对应 `backend/tests/ingestion/providers/ats_extractors/`、ATS/CDP/Parser 测试

### 步骤

1. 先运行 `rg` 确认生产代码、测试、脚本和文档中的所有引用。
2. 区分用户当前未提交的新文件和已跟踪旧文件；若候选文件仍含用户未提交工作，停止删除并报告冲突。
3. 只删除已经被新链路完全替代且无保留调用方的文件；`JobCoverageService`、模型、归一化和持久化不得删除。
4. 在 PowerShell 中逐个解析绝对路径，确认每个目标都位于 `D:\tools_dev\company_search\backend` 后，使用 `apply_patch` 删除文件；禁止对计算路径执行递归删除。
5. 删除对应无效配置和 `.env.example` 项：ATS 平台开关、Playwright 列表配置、自动 BOSS CDP 配置。
6. 更新 README，说明入口验证、JobHunt 安装/固定版本边界和 BOSS 手工导入命令。

### 验证

```powershell
rg -n "AtsProvider|AtsRenderer|ZhipinCdpCompanyProvider|DirectAtsPersistence|_ATS_DISCOVERY_TERMS" backend/app backend/tests
```

预期：无生产调用；若保留兼容代码，必须有明确注释和非生产测试理由。

## Task 12：全量验证与验收证据

### 文件

- 修改：`README.md`
- 修改：`docs/dev/job-coverage-at-scale-plan.md`
- 新增：`backend/tests/integration/test_efficient_recruiting_pipeline.py`

### 步骤

1. 集成测试覆盖：数据库新鲜命中、入口短路、一次搜索、JobHunt 单源成功、不支持停止、JobHunt 失败不回退、BOSS 手工导入。
2. 使用 fake HTTP、fake subprocess 和 fixture；断言无真实网络、浏览器或 npm 安装。
3. 运行迁移 upgrade/downgrade、完整后端测试、Ruff 和 mypy。
4. 检查 `git diff --check`、`git status --short` 和最终 diff，确保没有覆盖任务开始前的用户改动。
5. 在文档中记录实际通过数量、条件性跳过和未执行的真实在线 smoke；不得把离线测试描述为线上稳定性证明。

### 验证

```powershell
cd backend
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m mypy app
git diff --check
```

## 建议提交顺序

1. `test: define lightweight entry verification behavior`
2. `feat: add bounded recruiting entry verification`
3. `refactor: route collection through entry verification`
4. `feat: add jobhunt cli enumeration adapter`
5. `feat: persist complete external job snapshots`
6. `feat: add manual boss job import`
7. `refactor: remove legacy ats collection paths`
8. `docs: document efficient recruiting pipeline`

每个提交只包含对应任务文件；在工作区仍有用户改动时必须使用显式路径暂存并检查 `git diff --cached --name-only`。
