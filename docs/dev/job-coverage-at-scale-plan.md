# 万级公司职位覆盖设计

> **状态：Stage 3A 已完成；Stage 3B0 manifest 已冻结，entry-evidence smoke 已通过；Stage 3B Tasks 1–7 已完成，飞书/Moka 离线样本通过，Playwright 池生命周期受控；BOSS/猎聘/拉勾基础解析、安全页识别、Provider 阻断统计、platform cooldown 与默认关闭的 BOSS CDP 公司职位 Provider 已完成；所有新增采集默认关闭，在线 smoke 待 opt-in gate**
> **修订日期：2026-08-11**
> **定位：** Stage 3 的产品与技术设计，定义万级公司下职位覆盖、完整性、新鲜度和成本边界。
> **实施入口：** [migration-master-plan.md](migration-master-plan.md)
> **当前基线：** Stage 1、Stage 2 已合并到 `main`；Stage 3A 在隔离分支完成实现、最终审阅和当前矩阵，尚未集成；Stage 3B0 已冻结 1,000 家 canonical manifest，并完成 2 条公开证据的受限模型 smoke；Stage 3B 已完成离线 Provider 接入与默认关闭的 BOSS CDP 增强通道，真实在线 smoke、完整枚举和规模验证仍需单独审批。

## 1. 结论

当前系统已经能够存储和检索万级公司数据，并已用 10,000 家公司、100,000 个职位的生成数据验证查询 API 性能。该验证不包含外部网络、浏览器渲染、LLM、队列和数据库并发写入，因此不能证明万级采集能力。

Stage 3 的目标不是“全网实时全量职位库”，而是建立可测量、可降级、可持续迭代的职位覆盖系统：

- 招聘入口覆盖率达到 85% 以上；
- 成功职位列表枚举率达到 70% 以上；
- 头部 500 家公司完整列表率达到 90% 以上；
- 95% 的目标公司在 24 小时内完成一次成功列表刷新；
- 基础字段完整率达到 95% 以上；
- 失败或不完整的采集不能误关闭已有职位。

规模扩展必须按 1,000、3,000、10,000 家三道验收门逐级进行。任何固定刷新耗时都必须来自真实或获授权样本的端到端测试，不能从数据库查询压测推导。

## 2. 能力边界

### 2.1 当前已实现

| 能力 | 当前状态 | Stage 3 复用方式 |
|------|----------|------------------|
| 公司、别名、职位、来源和证据模型 | 已实现 | 作为新增覆盖模型的基础 |
| 幂等持久化和职位去重 | 已实现 | 保留 `(provider, source_raw_id)` 来源身份 |
| 安全 HTTP、SSRF 防护和 robots.txt | 已实现 | 所有新增 HTTP Provider 必须复用 |
| 知乎发现与公司官网 Provider | 已实现，默认关闭 | 用于入口发现和公开官网证据 |
| Orchestrator、Celery Worker/Beat | 已实现 | Stage 3 扩展为分层、分批调度 |
| 24 小时公司刷新与 30 天来源失效 | 已实现 | 增加快照完整性后改进关闭规则 |
| Redis 降级缓存 | 已实现 | 不作为采集状态事实源 |
| 采集任务状态和前端轮询 | 已实现 | 展示覆盖任务终态，不等待外部采集 |
| 10k 公司/100k 职位查询性能 | 已验证 | 仅证明查询容量，不证明采集吞吐 |
| ATS 阻断统计与 BOSS CDP 公司职位 Provider | 已实现，默认关闭 | 作为 BOSS 登录态本地只读增强通道，进入现有职位去重链路 |

### 2.2 已有验证资产但未接入生产

以下代码和测试属于可复用原型，不能作为生产完成项：

- ATS URL 分类；
- 自建招聘页 HTML 职位提取；
- Playwright 常驻渲染池；
- 飞书、Moka、百度、京东等页面适配器；
- URL 失效、自愈、ETag 和刷新实验；
- ICP、算法备案相关实验。

正式接入前必须适配当前 Provider、安全、配置、持久化、错误码和测试契约，并通过独立审阅。

### 2.3 明确不承诺

- 所有公司、所有职位和所有字段 100% 覆盖；
- 职位状态实时准确；
- 对登录、验证码、访问控制或反自动化机制进行绕过；
- 违反 robots.txt、站点服务条款或数据授权范围；
- 所有长尾 ATS 达到与主流平台相同的字段质量；
- 在容量测试前承诺一万家公司固定在 30–50 分钟内刷新完成。

## 3. 覆盖漏斗

```text
L0 公司集合
  -> 已有数据库、合规发现、种子和用户查询

L1 招聘入口
  -> 官方网站、公开招聘入口、ATS 平台和入口健康状态

L2 职位列表
  -> HTML、SSR、公开 API、分页、AJAX 或获授权的浏览器渲染

L3 职位详情
  -> 头部公司、高价值职位、关键字段缺失和用户按需补全
```

核心顺序是“先入口、再列表、后详情”。列表覆盖是规模化主战场；详情增强必须受预算控制。

## 4. 生产数据流

```text
Beat 选择到期公司
  -> 数据库分页扫描与优先级排序
  -> 分批创建 CrawlRun
  -> Celery 队列背压检查
  -> 入口发现或健康检查
  -> 静态 HTTP / ATS 渲染
  -> 确定性职位解析与分页枚举
  -> 仅对异常结果调用 LLM
  -> 归一化与去重
  -> 事务写入职位、来源和列表快照
  -> 更新覆盖指标和任务终态
```

用户搜索只读取数据库，不等待 Provider、Celery、Redis、浏览器或 LLM。

## 5. Provider 与解析边界

### 5.1 保留现有 Provider 安全边界

新增 Provider 必须继续满足：

- 只允许显式授权的 HTTP(S) 目标；
- 每次 DNS、IP、重定向和响应大小都经过校验；
- 遵守 robots.txt 和服务条款；
- 有明确超时、重试、速率限制和稳定错误码；
- 第三方内容按不可信数据处理；
- 不记录密钥、完整正文或未经清理的错误。

### 5.2 ATS 正式接入

ATS 原型不能直接复制到运行时。正式适配器必须：

1. 使用现有配置与授权开关；
2. 通过安全 HTTP 或受控 Playwright 获取页面；
3. 产出有界、可追踪的 `RawDocument` 证据；
4. 由确定性解析器生成结构化职位候选；
5. 记录页码、游标、来源声明总数和枚举是否完成；
6. 不支持的平台降级为保留招聘入口和稳定失败原因。

### 5.3 LLM 使用规则

- 常规 HTML/ATS 职位解析不调用 LLM；
- LLM 只用于异常结构、字段补全或模糊去重；
- 每次 LLM 调用必须有字符预算、超时、证据引用和可观测成本；
- LLM 不决定访问目标，不拥有数据库或任意工具。

### 5.4 BOSS CDP 公司职位增强通道

BOSS CDP 公司职位 Provider 是 Stage 3B 的默认关闭增强通道，不替代官网、飞书、Moka 和公开入口发现。该通道仅在用户显式启用、本机 Chrome 已通过 CDP 暴露且账号具备合法访问权限时运行。

当前实现边界：

- 通过本机 Chrome CDP 会话复用已登录浏览器上下文；
- 公司名先经过 BOSS 公司搜索，候选公司按规范化名称、品牌名、简称、主体名和法定名做相似度匹配；
- 匹配成功后按 brandId 分页读取该公司在 BOSS 当前可见职位；
- 输出 `ParsedJob` 并直入 `DirectAtsPersistence`，复用现有 `(provider, source_raw_id)` 和公司内语义去重；
- 通过 `ProviderFetchStats` 记录 `entries_discovered`、`pages_fetched`、`parsed_jobs`、`blocked_pages` 和稳定 `error_code`；
- 遇到登录、安全页、限流、接口结构变化或浏览器不可用时返回稳定错误码，不伪造零职位；
- 连续阻断达到阈值后进入 `platform_cooldown`，同轮不再继续请求 BOSS。

剩余边界：

- 默认测试只覆盖 fake CDP page 和固定 JSON 解析，不访问真实 BOSS；
- 真实在线 smoke 必须 opt-in，且只能在授权账号、本机浏览器和低频预算下运行；
- 该通道仍不能证明公司“全量招聘”，只能证明 BOSS 登录态当前可见职位；
- 不做验证码、滑块、安全页或访问控制绕过。

## 6. 列表快照与职位生命周期

每次职位列表枚举都必须产生快照，至少记录：

| 字段 | 含义 |
|------|------|
| `status` | `succeeded` / `partial` / `failed` |
| `pagination_complete` | 是否确认枚举到最后一页 |
| `empty_confirmed` | 零职位是否由成功完整响应确认 |
| `reported_total` | 来源页面或 API 声明的职位总数 |
| `observed_count` | 本次实际解析出的职位数 |
| `pages_fetched` | 成功获取的页面或游标批次数 |
| `content_fingerprint` | 用于判断列表是否发生变化 |
| `error_code` | 稳定、可公开的失败原因 |
| `started_at/completed_at` | 计算延迟和队列积压 |

关闭职位必须遵循：

- 完整快照中再次看到的来源重置缺失计数；
- 只有完整快照可以递增“连续缺失快照”计数；
- 连续两个完整快照均缺失时，允许关闭该入口对应来源；
- `partial` 或 `failed` 快照不关闭任何现有来源；
- `JobSource.lifecycle_managed` 持久记录完整生命周期是否已接管；legacy 和仅有 partial/failed 快照的来源继续使用 30 天兜底；
- 已由 applied complete 快照接管且入口仍保留的来源只按连续两个完整快照缺失规则关闭；删除入口后，存活来源重新具备 30 天兜底资格；
- 只要职位仍有一个有效来源，规范职位保持有效。

## 7. 数据模型方向

Stage 3 建议增加以下模型，最终字段以审批后的 Alembic 计划为准：

### 7.1 `job_entries`

一家公司可以有多个招聘入口。记录规范化 URL、平台、是否需要渲染、状态、连续失败次数、最近检查和最近成功时间。

### 7.2 `job_collection_snapshots`

记录入口级列表枚举结果、分页完整性、数量、耗时、错误和内容指纹。数据库行是覆盖状态事实源，不依赖 Celery 状态。

### 7.3 `job_sources` 生命周期扩展

增加 `job_entry_id`、`last_seen_snapshot_id`、`missing_complete_snapshots` 和默认 false 的 `lifecycle_managed`。前两个可空外键在目标删除时 `SET NULL`；持久 marker 区分 legacy/partial-only 的 30 天兜底与 applied complete 的两次完整缺失生命周期，避免从可空外键反推管理状态。

### 7.4 `job_details`

选择性存储部门、学历、经验、任职要求、福利、标签和最近增强时间，避免把列表主表演化为不受控的大宽表。

现有职位类型保持：`full_time`、`part_time`、`internship`、`temporary`、`campus`、`experienced`、`unknown`。

## 8. 覆盖指标口径

| 指标 | 口径 |
|------|------|
| 招聘入口覆盖率 | 至少有一个 active 入口的目标公司 / 全部目标公司 |
| 成功列表枚举率 | 最近 24 小时存在 succeeded 或可信 empty 快照的公司 / 有 active 入口公司 |
| 完整列表率 | 最近成功快照 `pagination_complete=true` 的公司 / 成功枚举公司 |
| 零职位确认率 | `empty_confirmed=true` 的公司 / observed_count 为 0 的成功公司 |
| 数量一致率 | `reported_total` 为空或等于 `observed_count` 的完整快照 / 完整快照 |
| 基础字段完整率 | title、city、apply_url 均有效的职位 / 本次发现职位 |
| 详情字段完整率 | 目标详情字段达标职位 / 进入详情增强池职位 |
| ATS 成功率 | 指定平台成功完整枚举入口 / 该平台活跃入口 |
| 刷新 SLO | 24 小时内成功刷新公司 / 目标公司 |
| 队列滞后 | 当前时间减最早待处理任务创建时间 |

成功枚举零职位不算覆盖失败；未完整分页不能算列表完整。

## 9. 调度与容量

### 9.1 调度原则

- 使用数据库游标分页选择到期公司，不一次加载全部公司；
- 按 `head`、`mid`、`long_tail` 和失败恢复分队列或优先级；
- 创建任务前检查队列深度和最老任务年龄；
- 达到背压阈值时停止派发，不制造无界积压；
- Provider 和目标域名都要限流；多 Worker 使用共享预算；
- 每个批次记录计划数、派发数、完成数、失败数和耗时。

### 9.2 规模门

| 阶段 | 公司数 | 最低通过条件 |
|------|--------|--------------|
| Gate 1 | 1,000 | 连续 7 天刷新 SLO ≥95%，无失控积压，无错误批量关闭职位 |
| Gate 2 | 3,000 | 连续 14 天稳定，入口 ≥85%，列表枚举 ≥65%，容量余量 ≥30% |
| Gate 3 | 10,000 | 连续 14 天稳定，入口 ≥85%，列表枚举 ≥70%，头部完整率 ≥90% |

每个 Gate 必须报告静态页面、动态 ATS、零职位、分页、失败和降级样本分布。未达标时先修复覆盖或容量瓶颈，不扩大规模。

### 9.3 数据库与部署

- SQLite 用于本地开发、迁移测试和确定性测试；
- 万级多 Worker 验收以 PostgreSQL 为生产目标；
- Redis 用于 Celery 传输、共享限流或短期协调，但数据库仍是任务和覆盖状态事实源；
- 必须验证重复投递、Worker 崩溃、数据库重连和迁移升级。

## 10. 合规与降级

- 不通过代理轮换、身份轮换或浏览器伪装绕过封禁；
- 网络代理只能用于明确批准的基础设施出口，不用于规避访问控制；
- 遇到登录、验证码、访问拒绝或 robots.txt 禁止时停止采集并记录稳定状态；
- 动态 ATS 不可采集时保留公开入口，不伪造零职位；
- 可按 Provider、平台和域名关闭渠道；
- 失败渠道不影响已有搜索数据读取。

## 11. Stage 3 交付阶段

1. **覆盖观测基础：** 列表入口、快照、指标和安全职位关闭。
2. **ATS 正式接入：** 将已验证原型适配当前生产契约。
3. **完整列表枚举：** 标准化页码、游标、滚动和 AJAX 分页。
4. **规模化调度：** 分批扫描、优先级、背压和共享限流。
5. **详情增强：** 头部与高价值职位的受预算补全。
6. **规模验收：** 依次通过 1k、3k、10k Gate。

详细任务、迁移顺序和审批门见 [migration-master-plan.md](migration-master-plan.md)。

## 12. 审批门

Stage 3A 已按获批的详细 implementation plan 完成，七项计划任务及 Task 7 审阅全部通过。Company Identity Resolution Hardening 的离线、PostgreSQL、严格 10,000-company performance、secret baseline 与专用只读审计数据库本地 gate 均已通过。Task 10 已完成 reviewed candidate import、identity resolution、review/audit gate、canonical manifest freeze 与受限 entry-evidence smoke。Stage 3B Tasks 1-7 已完成离线接入；BOSS/猎聘/拉勾基础解析、安全页识别、Provider 阻断统计、platform cooldown 与默认关闭的 BOSS CDP 公司职位 Provider 已完成。真实在线 smoke、完整覆盖快照枚举、前端看板和规模扩容仍需 opt-in gate 与单独审批。

## 13. Stage 3A 实施记录

截至 2026-08-05，隔离分支已实现：

- 契约：`758078b`、`2bc5a45`、`1d15f46`；
- `0006`：`5067b4c`、`218fffd`、`11a4a11`；
- `0007`：`c4f090b`、`f778ec1`；
- 覆盖 repository：`b1fea8e`、`bd4f5ec`、`b7ffe3d`、`a0e5b65`；
- 原子生命周期 service：`2d3f166`、`5936b41`、`eae6055`；
- 覆盖报告与 JSON CLI：`809bafa`、`3b208ae`、`5c30753`；
- Task 7 gate 与最终修复：`b9eb888`（集成验收与 gate）、`eb55e77`（PostgreSQL 清理）、`5167fbc`（生命周期加固）、`13ad3ca`（快照所有权）、`cc2a760`（锁刷新）、`83a8f14`（持久 `lifecycle_managed`）。

Tasks 1–7 均已完成。Task 7 及全分支最终审阅在 round 4/5 后得到 specification PASS 和 quality APPROVED，且没有开放的 Critical、Important 或 Minor finding。当前 `83a8f14` 验证结果为 Ruff clean、mypy 79 个源文件 clean、backend `539 passed / 2 skipped / 2 deselected`、integration `13 passed`、performance `2 passed / 541 deselected`；offline Alembic upgrade/downgrade clean；live PostgreSQL `2 passed / 17 deselected`，结束后 `stage3a_test_*` schema 残留为零。

当前 Provider 测试与静态引用审计证明 Provider 不调用 `RecordJobSnapshot` 或 `JobCoverageService`，因此不会伪造完整列表或可信空列表。默认测试不访问真实网络、浏览器、Redis 或 LLM。唯一 warning 来自既有的非整数 `salary_months` 负向测试，已如实保留在 gate 证据中。

## 14. Company Identity Resolution Hardening 与 Task 10 release gate

获批执行基线为用户覆盖指定的 `5d6f2cf`；实现提交如下，最终 whole-branch review 仍必须覆盖原始计划基线 `2143f8f..HEAD`：

- Task 1 contracts：`64d1a3e`、`2427ecf`；
- Task 2 review schema 与迁移 `0009_company_identity_review`：`104ef3d`、`23de812`；
- Task 3 resolution/evidence/concurrency：`906d3b4`、`7d7415a`、`bab4ab9`、`ff922fc`、`d7730ae`；
- Task 4 audited decisions：`b0890fd`、`6ef0235`、`53792cd`；
- Task 5 ingestion stop/ownership serialization：`cb2c0b6`、`d9d98d1`、`ca01958`；
- Task 6 read-only audit：`ad1ddb9`、`99c4cb4`；
- Task 7 operator CLI 与 atomic output hardening：`c4ec697`、`4efb39b`、`249c32d`。
- Task 8 offline-gate repair：`5464a92`（保留备案号原始展示值）、`8153f64`（规范化备案 identity 唯一性与迁移 preflight）、`2f71395`（显式捕获 intentional serializer warning）。
- Task 8 PostgreSQL/performance gate closure：`c5de19b`（稳定 `pg_trgm` extension schema）、`97e2478`（覆盖 `pg_trgm` schema edge cases）、`1e0f5ea`（修复 PostgreSQL benchmark harness）、`f5e1ab5`（强制精确 performance company count）。

已占用迁移编号为 `0008_gate1_manifest_discovery`、`0009_company_identity_review`、`0010_entry_evidence_rounds`、`0011_entry_evidence_integrity`；计划中的 `job_details` 与 `coverage_query_indexes` 顺延为 `0012`、`0013`。

身份审计的确定性分类为：Critical：`cross_table_name_owner`、`shared_website_identity`、`incompatible_recruitment_identities`、`audit_findings_truncated`；Important：`accepted_candidate_name_unrepresented`、`fuzzy_name_cluster`、`orphan_alias`、`pending_review_owner_changed`、`similarity_search_unavailable`；Minor：`canonical_name_normalized_drift`、`alias_normalized_drift`、`filing_number_normalized_drift`、`website_normalized_drift`。Task 6 的人工裁定采用窄 pending-owner 语义：只报告可证明的新当前所有权冲突或基数变化，不扩展 schema 去表达此前 exact owner UUID。Task 7 的人工裁定把 POSIX 输出目录视为可信、由 operator 控制；Windows 使用更强的 pinned native handles，但不宣称消除 POSIX 同权限恶意写入者在每个 namespace syscall 间竞态的风险。

2026-08-08 final Task 8 证据为：identity/ingestion/manifest/review-stop/migration 离线组 `729 passed / 8 conditional skips`；provider/coverage/API 离线组 `221 passed`；Ruff clean；mypy `98 source files` clean。PostgreSQL migration/service marker 为 `5 passed / 58 deselected`，所选测试没有 skip；PostgreSQL performance 文件为 `5 passed`，没有 skip。严格性能数据集包含 9,975 个 regular Companies 和 25 个 boundary Companies，合计恰好 10,000 个 Companies，并包含 `9,975 aliases`。`pg_trgm` 位于 `public` namespace，测试自有的非 `CASCADE` 清理后 `identity_resolution_*` schema 残留为零。

规定的 all-tracked secret pattern scan 仍只包含以下六项 tracked baseline synthetic examples/tests；复核仅记录 path、line、SHA-256，没有输出匹配值。六项均未增加、删除、移动或改变哈希，`9df90a6..f5e1ab5` changed-range scan 为零命中，因此无需修改无关 fixture；此后任何新增、删除、移动或哈希变化都必须重新审阅：

| Path | Line | SHA-256 |
| --- | ---: | --- |
| `.agents/skills/subagent-driven-development/SKILL.md` | 57 | `8de105e3bd07359ea603d72d5c6cec36480c7db49258606292d16376d4c2e7f2` |
| `.agents/skills/subagent-driven-development/SKILL.md` | 84 | `3f608c056a6083f751e877730521334b08330c2edf7e2d8495bea612073d68b4` |
| `.agents/skills/subagent-driven-development/SKILL.md` | 85 | `b84c521a3403fe31c687e7d95e63017eacbfb08839f05d63cb8b6ae733373d7b` |
| `.agents/skills/subagent-driven-development/SKILL.md` | 300 | `ba8434c8179a46144aa389d9d2dfad302e4fa20b34f74aa9b6582b971787f00a` |
| `backend/tests/company_identity/test_cli.py` | 436 | `3413a723213180695b84a3b7b43b96e164b6a54c9f54788433ec2643aa80d0f8` |
| `backend/tests/manifest/test_reporting.py` | 263 | `aea4bf9f6063bf94a2d3c373aad60cfc383d45415bc73da5f502b44780b916bb` |

专用只读 audit 数据库已升级到 `0009_company_identity_review`，sanitized CLI 报告零 findings。外部 audit report 未被读取或提交。

### Task 10 Stage 3B0 数据 gate（2026-08-09）

候选源 registry 在提交 `1996a7b`、`095f555`、`1a8f33c`、`4ac898d`、`0d18c47`、`79e0c26`，以及随后移除不适用来源的提交后固定为 45 项：44 个 `candidate_pool`、1 个 `entry_discovery_fallback`；source class 为 19 个 government、25 个 association、1 个 authorized API。外部 reviewed JSONL 在排除 2 个叙述性误提取后包含 5,111 个 exact identity，SHA-256 为 `8839e91442378224a2a5df9120561eb00080d000711b5990e53ab5eb221dfc6d`。候选主分类分布为 chips/compute 47、cloud/model platforms 46、autonomous transport 80、computer vision 1,236、data/MLOps 524、enterprise/vertical AI 801、foundation models 784、robotics 70、speech/language 1,523；九类均满足 allocation floor。

专用 PostgreSQL 18 数据库处于 `0009_company_identity_review`，import 创建并自动接受 5,111 项，review-required、rejected、accepted-but-unresolved 均为 0；candidate review queue 与 identity review queue 均为 0，填充后 company identity audit 为 0 findings。5,111 家公司的 city 全部未提供，scale 全部为 `unknown`，不得将其误报为已取得真实 city/scale 分布。

canonical manifest version 为 `abaad7965cabbaaa09e2dab6013be11c8b26d112e1444c18c36a8bb68bf584c4`，包含 1,000 个唯一 company id 与 1,000 个唯一 position。最终 allocation 为 chips/compute 45、cloud/model platforms 44、autonomous transport 49、computer vision 197、data/MLOps 105、enterprise/vertical AI 141、foundation models 139、robotics 47、speech/language 233。tracked manifest 为 230,607 bytes，SHA-256 `8ba503a77a37c18d2c1ddf8792fc161e423597d0b1e38e4630cddb9bdef52c81`；tracked quota 为 1,308 bytes，SHA-256 `343b3cf9ce5849d88b158f252953d1ad38273f99058e91693f09a552d2bc54e8`。tracked 文件与三个独立 external freeze replay 均 byte-identical。

legacy discovery 已完成 1,000 条 `not_found`，未发出 Zhihu 请求。随后新增不可变 entry-evidence round `evidence-smoke-20260809`：从已登记 CAGD 证据关联到七牛云与万兴科技的公开官方招聘页，DashScope `qwen-plus` 共调用 2 次，得到 2 条 `accepted` self-hosted entry；2 条均进入分层抽检并完成审计，严重误判 0、暂停分层 0。aggregate entry coverage 为 2/1,000（0.2%）。本结果只证明受限证据流水线可运行，不代表整体覆盖率目标已达成；不得请求或枚举 job list。新增迁移 `0010_entry_evidence_rounds` 与 `0011_entry_evidence_integrity`；后者冻结 round membership、保存重放证据与模型判断、对严重误判追加 quarantine，并在数据库层强制 append-only。加固后的外部证据若缺少进程内 robots/ownership 验证只能进入 `review_required`。原计划 job details 与 coverage indexes 顺延为 `0012`、`0013`。Stage 3B 离线 Provider 接入与默认关闭 BOSS CDP 增强通道已完成；真实在线 smoke、完整枚举和规模验证仍需单独审批。

## 15. 最近验证记录

2026-08-11：

- `tests/ingestion/providers/test_zhipin_cdp.py`：7 passed，覆盖 fake CDP page、公司搜索解析、职位分页解析、登录页识别、公司匹配、默认关闭、阻断熔断和低置信拒绝；
- `tests/ingestion/providers/test_ats.py` 与 `tests/ingestion/test_orchestrator.py`：覆盖 ProviderFetchStats、BOSS/ATS 阻断诊断透传和 platform cooldown；
- `tests/ingestion + tests/core`：476 passed, 3 skipped；
- `ruff check`：核心改动文件通过。

## 16. 仍需跟踪风险

仍需跟踪的 Minor/运行风险：advisory lock keys 尚未排序去重，理论上存在 64-bit hash collision 下的锁顺序风险；既有 seed importer 直接写 `RegulatoryFiling` 的路径在 Task 3 persistence locking 范围外；100-company audit chunk 的 common evidence 可能占用 display slot，`display_names` 可能不完整；POSIX 分支缺少项目依赖齐全的 runtime 验证，Windows symlink 测试又受当前账户权限限制；POSIX 同权限 writer 的剩余竞态按人工裁定保留。上述风险不得被 Task 10 数据执行静默覆盖。
