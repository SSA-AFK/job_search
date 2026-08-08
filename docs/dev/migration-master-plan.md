# Stage 3 万级职位覆盖迁移总计划

> **状态：Stage 3A 已完成；Company Identity Resolution Hardening 已实现，Task 10 release gate 阻塞**
> **修订日期：2026-08-08**
> **定位：** 基于已完成 Stage 2 的 Stage 3 执行路线；Stage 3A 已按获批详细计划实施，后续阶段仍不是可直接开工的 implementation plan。
> **设计依据：** [job-coverage-at-scale-plan.md](job-coverage-at-scale-plan.md)
> **审批规则：** 本文批准后仍需生成详细 implementation plan，并再次获得执行授权。

## 1. 当前基线

### 1.1 已进入正式基线

- FastAPI 搜索与公司详情 API；
- SQLAlchemy 模型和 Alembic `0001`–`0009`（当前 hardening 分支）；
- 公司、职位、来源、证据、备案、采集请求和运行模型；
- 幂等持久化、规范化和确定性优先去重；
- SSRF 安全 HTTP、知乎和公司官网 Provider；
- 结构化抽取、Orchestrator、Celery Worker/Beat；
- 24 小时公司刷新、任务重派和 30 天职位来源失效；
- Redis 降级缓存和前端采集轮询；
- 10k 公司/100k 职位的查询性能门。
- 招聘入口、完整性快照、安全来源生命周期和内部覆盖 JSON 报告。

### 1.2 可复用但未正式集成

- `AtsClassifier`；
- `JobHtmlExtractor`；
- Playwright `PoolRenderer` 与可点击渲染；
- 飞书、Moka、百度、京东等平台解析器；
- URL 校验、失效、自愈、ETag 和并发刷新实验；
- ICP 与算法备案实验模块。

这些资产必须按当前代码契约重新测试和接入，不能因为文件位于 `backend/app/` 就视为生产完成。

### 1.3 当前关键缺口

1. ATS 原型未注册到生产运行时；
2. 当前 Provider 尚未产出可证明分页完整性的快照；
3. 当前日调度逐公司提交和派发，缺少批处理与背压；
4. 限流仅为单进程 Provider 级；
5. 常规职位抽取仍可能依赖 LLM；
6. 尚无队列滞后和误关闭保护前端看板；
7. 尚无真实 1k、3k、10k 端到端采集基准。

## 2. 迁移原则

- 从现有 Stage 2 架构增量演进，不重建 `backend/`；
- 新 Alembic revision 从 `0006` 开始；
- Provider、安全 HTTP、持久化和任务状态契约保持向后兼容；
- 原型代码必须通过适配器进入生产边界，不直接复制测试实现；
- 常规职位解析优先确定性逻辑，LLM 仅作受预算 fallback；
- 只有完整列表快照能够关闭缺失来源；
- 数据库是覆盖和任务状态事实源；
- SQLite 保持开发兼容，生产规模门使用 PostgreSQL；
- 每个阶段完成独立测试、全量回归、安全审阅和用户审批；
- 未通过前一规模门不得扩大公司数量。

## 3. 目标文件结构

以下是规划边界，详细 implementation plan 可以在不改变职责的前提下调整文件名：

```text
backend/app/
├── models/
│   ├── job_entry.py                # 招聘入口
│   ├── job_snapshot.py             # 列表快照
│   └── job_detail.py               # 选择性详情
├── ingestion/
│   ├── coverage/
│   │   ├── contracts.py            # 快照与分页结果契约
│   │   ├── repository.py           # 覆盖状态读写
│   │   └── lifecycle.py            # 来源关闭/恢复规则
│   ├── jobs/
│   │   ├── parser.py               # 确定性解析协议
│   │   ├── pagination.py           # 页码/游标/滚动状态
│   │   └── enrichment.py           # 详情增强策略
│   └── providers/
│       ├── ats.py                  # ATS Provider 组合边界
│       ├── ats_classifier.py       # 原型适配后接入
│       ├── ats_renderer.py         # 受控 Playwright 池
│       └── ats_extractors/          # 平台独立解析器
├── tasks/
│   ├── job_dispatch.py             # 分页扫描与分批派发
│   ├── job_refresh.py              # 列表刷新任务
│   └── job_enrichment.py           # 详情增强任务
└── coverage/
    ├── schemas.py                  # 指标输出
    ├── repository.py               # 聚合查询
    └── service.py                  # SLO 和 Gate 报告
```

测试按同一业务边界放在 `backend/tests/`，原根目录 `tests/` 作为历史验证资产保留到 Stage 3 全部通过，不在本计划中删除。

## 4. 数据库迁移序列

### `0006_job_entries_and_snapshots`

- 创建 `job_entries`；
- 创建 `job_collection_snapshots`；
- 添加公司、状态、到期时间和平台索引；
- 同时支持 SQLite 与 PostgreSQL；
- 对现有公司不伪造 active 招聘入口。

### `0007_job_source_snapshot_lifecycle`

- `job_sources` 增加 `job_entry_id`；
- 增加 `last_seen_snapshot_id`；
- 增加 `missing_complete_snapshots`，默认 0；
- 增加 `lifecycle_managed`，默认 false，持久区分 30 天 fallback 与 applied complete 生命周期；
- 迁移现有数据时保留当前 active 状态；
- 外键和唯一约束在 SQLite 重建表时保持完整。

### `0008_gate1_manifest_discovery`

- 创建候选事实、review decision、manifest/member 与 entry discovery observation 表；
- 保留 Stage 3A 行与外键，Task 10 仍不得在 release prerequisites 未通过时执行真实候选导入或 live discovery。

### `0009_company_identity_review`

- 创建 company identity review item 与 append-only decision audit；
- 为公司、别名和备案证据增加规范化 identity keys 与约束；
- PostgreSQL 增加 `pg_trgm` capability/index DDL，SQLite 保持离线兼容；
- 所有新增 audit-history 外键使用 `ON DELETE RESTRICT`。

### `0010_job_details`

- 创建一对一 `job_details`；
- 存储部门、学历、经验、要求、福利、标签和增强时间；
- 长文本和标签设置明确长度/数量上限。

### `0011_coverage_query_indexes`

- 根据 Gate 1 的真实查询计划增加必要索引；
- PostgreSQL 使用经过 `EXPLAIN` 证明的部分索引；
- 不为未经使用的假设查询提前增加索引。

每个 revision 必须验证：空库升级、从 `0005` 升级、downgrade、SQLite 外键、PostgreSQL 约束和现有数据保留。

## 5. Stage 3A：覆盖观测基础

**目标：** 先让“抓到了多少、是否完整、为什么为空”成为数据库事实。

### 交付范围

- 实施 `0006` 和 `0007`；
- 定义列表快照、分页状态和覆盖错误码；
- 写入 succeeded、partial、failed 和可信 empty 快照；
- 实现连续完整快照缺失两次才关闭来源；
- legacy 和仅有 partial/failed 快照的来源保留 30 天失效兜底；applied complete 生命周期来源在入口保留时使用两次完整缺失规则；
- 增加内部覆盖率查询和 Gate 报告，不先建设复杂前端看板。

### 验收

- 成功零职位与解析失败可区分；
- partial/failed 快照不会关闭现有来源；
- 两个连续完整快照缺失才关闭对应来源；
- 其他来源仍有效时规范职位保持 active；
- 重复快照和任务重投幂等；
- 迁移从 `0005` 在 SQLite/PostgreSQL 都通过。

### 停止条件

若现有职位无法无损迁移，或关闭规则可能因失败快照误删职位，停止进入 Stage 3B。

### 实施状态（2026-08-05）

Tasks 1–7 均已实现并通过审阅。Task 7 gate 与最终修复提交为 `b9eb888`（集成验收与 gate）、`eb55e77`（PostgreSQL 清理）、`5167fbc`（生命周期加固）、`13ad3ca`（快照所有权）、`cc2a760`（锁刷新）、`83a8f14`（持久 `lifecycle_managed`）。

`83a8f14` 当前矩阵：Ruff clean；mypy 79 个源文件 clean；backend `539 passed / 2 skipped / 2 deselected`；integration `13 passed`；performance `2 passed / 541 deselected`；offline Alembic upgrade/downgrade clean；live PostgreSQL `2 passed / 17 deselected`，清理后 `stage3a_test_*` schema 残留为零。Provider 目录没有 Stage 3A 快照写入调用，默认 suite 不依赖网络、浏览器、Redis 或 LLM；backend 全量测试保留一个既有非整数 `salary_months` 负向测试的 intentional Pydantic serializer warning。

Task 7 及全分支最终审阅在 round 4/5 后得到 specification PASS 和 quality APPROVED，没有开放的 Critical、Important 或 Minor finding。Stage 3A 状态为“已完成”；Stage 3B 状态为“等待单独实施计划与审批”，不得在本 gate 中开始。

### Company Identity Resolution Hardening 状态（2026-08-08）

用户覆盖指定的获批执行基线为 `5d6f2cf`；最终 whole-branch review 仍以 `2143f8f..HEAD` 为范围。实现提交按任务为：Task 1 `64d1a3e`、`2427ecf`；Task 2 `104ef3d`、`23de812`；Task 3 `906d3b4`、`7d7415a`、`bab4ab9`、`ff922fc`、`d7730ae`；Task 4 `b0890fd`、`6ef0235`、`53792cd`；Task 5 `cb2c0b6`、`d9d98d1`、`ca01958`；Task 6 `ad1ddb9`、`99c4cb4`；Task 7 `c4ec697`、`4efb39b`、`249c32d`。

Task 6 audit categories 固定为：Critical `cross_table_name_owner`、`shared_website_identity`、`incompatible_recruitment_identities`、`audit_findings_truncated`；Important `accepted_candidate_name_unrepresented`、`fuzzy_name_cluster`、`orphan_alias`、`pending_review_owner_changed`、`similarity_search_unavailable`；Minor `canonical_name_normalized_drift`、`alias_normalized_drift`、`filing_number_normalized_drift`、`website_normalized_drift`。人工裁定保留窄 pending-owner 语义，只报告可证明的新当前所有权或基数变化，不增加 prior exact-owner UUID schema。Task 7 人工裁定把 POSIX output directory 作为可信 operator-controlled boundary；Windows 保持 pinned native-handle 保护，POSIX 同权限 writer 的 syscall 间竞态是明确剩余风险。

Task 8 的完整离线命令结果为：第一组 `728 passed / 7 skipped / 1 warning`；第二组 `220 passed / 1 failed`，失败为 company detail API 的备案号原始展示值被 normalization 改写；Ruff clean；mypy `98 source files` clean。warning 是既有 intentional 非整数 `salary_months` Pydantic serializer warning，尚需 warning assertion 或无 warning 修复。默认测试未启用网络、Redis、模型 API、浏览器或职位列表 provider。tracked-file secret pattern scan 命中 tracked agent skill 文档中的四条 API-key pattern，以及 CLI/reporting redaction 测试中的两条 credentialed-connection-URI pattern；只记录 file/line/category，未输出匹配值，因此该 gate 不是 clean。

由于 `TEST_POSTGRES_URL` 不存在，`0009` live migration round trip、Task 3/4/5 两会话 concurrency、trigram plan 和严格 10,000-company performance marker 均未运行，不能声明 no-skip 或零 residual isolated schemas。专用 read-only audit 数据库配置也不存在，所以 audit CLI 未运行，Critical/Important clean 状态未知。Task 10 保持 paused，且不得生成真实 candidate import、live discovery、manifest artifacts 或 runtime reports。

Task 10 release prerequisites 是：离线 suites 零失败；既有 warning 被消除或显式断言；tracked-file secret pattern scan clean；PostgreSQL migration/service 与 `performance and postgresql` markers 零 skip 并由测试自有的非 `CASCADE` 清理验证零残留 schema；专用数据库 audit 的所有 Critical/Important 为零或具有明确人工裁定；`2143f8f..HEAD` whole-branch review clean。剩余 Minor 包括 advisory lock key 未排序去重的理论 hash-collision deadlock、seed importer direct `RegulatoryFiling` writer 不在 Task 3 locking 内、audit chunk display capacity、POSIX runtime 未实测、Windows symlink privilege skip，以及人工接受的 POSIX 同权限竞态风险。

## 6. Stage 3B：ATS 正式接入

**目标：** 将已有 ATS 验证资产接入 Stage 2 的安全生产链路。

### 交付范围

- 为 ATS 分类、静态 HTML、Playwright 和平台解析器定义适配器；
- 所有外部访问复用安全 URL、DNS/IP、重定向、大小和超时边界；
- Provider 产出有界 `RawDocument` 证据；
- 确定性解析器生成职位候选和列表枚举元数据；
- 第一批只启用已有离线样本覆盖充分的平台；
- 每个平台有独立开关、授权说明、并发和错误码；
- 登录、验证码、robots.txt 禁止或访问拒绝时停止并降级为入口记录。

### 验收

- 飞书和 Moka 离线固定样本完整通过；
- 获授权在线 smoke test 默认不在 CI 运行；
- 浏览器池异常不会导致 Worker 泄漏或任务永久 running；
- ATS 失败不影响已有数据库搜索；
- 常规成功路径不调用 LLM；
- 未授权平台保持关闭。

### 停止条件

若适配需要绕过访问控制，或无法纳入现有安全 HTTP/运行时契约，不启用该平台。

## 7. Stage 3C：完整列表枚举

**目标：** 让“列表完整”成为可验证结论，而不是 `observed_count > 0`。

### 交付范围

- 统一页码、游标、滚动和 AJAX 枚举状态；
- 设置每入口最大页数、最大职位数、总字节和总时间预算；
- 记录 `reported_total`、`observed_count` 和 `pagination_complete`；
- 去重后仍保留来源声明数量差异；
- 内容未变化时允许跳过结构化解析，但仍写成功快照；
- 解析器升级不得改变旧来源身份。

### 验收

- 多页固定样本验证第一页、中间页和末页；
- 游标循环、重复页、总数变化和中途失败均有稳定结果；
- 达到预算上限时状态为 partial，不伪装 complete；
- 可信空列表必须来自成功且完整的响应；
- 头部样本可输出数量差异报告。

## 8. Stage 3D：规模化调度

**目标：** 在不制造无界队列、不突破站点预算的前提下刷新公司集合。

### 交付范围

- 数据库游标分页选择到期公司；
- 按 head、mid、long_tail 和 recovery 分优先级；
- 批次化创建和派发任务；
- 设置队列深度、最老任务年龄和失败率背压阈值；
- 实现 Provider + 域名维度的跨 Worker 共享速率预算；
- Worker 崩溃和重复投递继续使用当前 claim-token 契约；
- 记录批次计划、派发、完成、失败、跳过和耗时。

### 验收

- 选择 10,000 家时内存保持有界；
- 重复调度不产生重复 active run；
- 达到背压阈值后停止派发并可自动恢复；
- 多 Worker 不突破相同域名预算；
- PostgreSQL 并发写入无锁顺序回归；
- Redis/Worker 故障后数据库状态可恢复。

## 9. Stage 3E：选择性详情增强

**目标：** 提升信息深度，但不让成本从公司级无界放大到职位级。

### 交付范围

- 实施 `0008`；
- 按公司层级、职位价值、字段缺失和用户请求选择增强对象；
- 为每家公司和每日设置详情预算；
- 确定性解析部门、学历、经验、要求、福利和标签；
- LLM fallback 有调用上限、超时、成本和证据记录；
- 详情失败不影响列表职位可搜索和投递。

### 验收

- 预算耗尽后任务稳定停止，不影响列表刷新；
- 同一详情内容不重复调用 LLM；
- 头部样本详情字段完整率达到 50% 以上；
- 长文本、标签数量和模型输出均有界；
- 不可信页面文本不能改变工具或访问目标。

## 10. Stage 3F：规模验收

### Gate 1：1,000 家

- 使用真实或明确授权的分层样本；
- 连续运行 7 天；
- 24 小时刷新 SLO ≥95%；
- 无失败快照误关闭职位；
- 报告入口、平台、分页、零职位和字段质量分布；
- 记录端到端耗时、队列滞后、浏览器占比、LLM 调用和数据库写入。

### Gate 2：3,000 家

- Gate 1 全部通过；
- 连续运行 14 天；
- 招聘入口覆盖率 ≥85%；
- 成功列表枚举率 ≥65%；
- 容量余量 ≥30%；
- 单个平台失败不会拖垮其他队列。

### Gate 3：10,000 家

- Gate 2 全部通过；
- 连续运行 14 天；
- 招聘入口覆盖率 ≥85%；
- 成功列表枚举率 ≥70%；
- 头部 500 家完整列表率 ≥90%；
- 基础职位字段完整率 ≥95%；
- 95% 的公司在 24 小时内成功刷新；
- 通过 PostgreSQL 多 Worker、任务重投、恢复和迁移验证。

任何 Gate 未通过都返回对应阶段修复，不通过增加 Worker 数量掩盖覆盖或正确性问题。

## 11. 测试与审阅矩阵

| 范围 | 必须覆盖 |
|------|----------|
| 单元测试 | 分页、快照状态、零职位、关闭规则、预算和错误码 |
| Provider 合同 | SSRF、重定向、大小、超时、robots、登录/CAPTCHA 降级 |
| 集成测试 | API → Celery → Provider → 解析 → 持久化 → 快照终态 |
| 幂等与并发 | 重复派发、Worker 重投、相同来源、迁移并发和锁顺序 |
| 容量测试 | 调度扫描、队列派发、数据库写入、浏览器池和共享限流 |
| 降级测试 | Redis、LLM、单 Provider、浏览器和数据库短暂不可用 |
| 迁移测试 | 空库、`0005` 升级、downgrade、SQLite 和 PostgreSQL |
| 审阅 | 每阶段规格符合性、代码质量、安全与合规审阅 |

外部在线测试必须显式 opt-in，不在默认 CI 中访问真实站点。

## 12. 配置方向

新增配置必须使用现有 Settings 模式，并在详细 implementation plan 中确定名称。至少覆盖：

- ATS 平台级启用开关；
- Playwright 池大小、并发、超时和重启阈值；
- 列表最大页数、职位数、字节和总时间；
- 调度批大小、队列背压阈值和优先级；
- 域名共享速率预算；
- 详情增强每日预算和 LLM fallback 预算；
- 覆盖快照保留周期；
- Gate 报告样本范围。

所有 Provider 密钥只在对应 Provider 启用时必填，真实密钥不得提交。

## 13. 历史验证资产处理

- 根目录 `tests/` 在 Stage 3 验收前保持原样；
- 原型测试用于提取行为和样本，不直接定义生产架构；
- 与当前契约冲突时，以已批准设计和 Stage 2 生产契约为准；
- Stage 3 全部通过后，是否归档或删除历史测试需单独审批；
- 本计划不授权任何递归删除。

## 14. 审批与执行门

1. 审批 [job-coverage-at-scale-plan.md](job-coverage-at-scale-plan.md)；
2. 审批本文的阶段、迁移和验收边界；
3. 为 Stage 3A 编写逐步骤 implementation plan；
4. 再次取得执行授权；
5. 创建隔离工作树并从测试开始实施；
6. Stage 3A 完成审阅后，Stage 3B 仍须等待单独实施计划与审批。

本文档获批不等同于授权实施。未经明确执行授权，不修改生产代码、不运行外部采集、不创建规模数据集。
