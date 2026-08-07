# 公司实体识别与主数据加固设计

> 日期：2026-08-07
> 基线分支：`codex/gate1-local-benchmark-design`
> 基线提交：`ac1590b773123b88349df33fe15415ac2ca25f6e`
> 状态：设计已完成，Task 10 在本设计实施并通过门禁前保持暂停

## 1. 背景

项目当前存在两条公司身份处理路径：

1. Stage 3B0 manifest 路径通过 `CandidateFact`、精确名称/别名、招聘身份和人工审核解析公司身份。该路径已将模糊名称命中降级为 `review_required`，会持久化候选别名，并且不会由普通导入覆盖已有公司的主名称。
2. 旧 ingestion runtime 仍使用 `CompanyDeduplicator`。它在精确名称/别名未命中时扫描全部公司，以 RapidFuzz `ratio >= 80` 自动选取一个公司；分数相同时以 UUID 排序决定结果。后续 `PersistenceService` 会用新候选覆盖已有公司的 `canonical_name` 和 `normalized_name`。

两个路径共享 `companies`、`company_aliases`、职位、备案和来源关系。即使 Task 10 本身使用较安全的 Stage 3B0 路径，旧 ingestion 仍可能在后续无人值守任务中误合并公司、改变主名称，并污染已经冻结的 manifest、招聘入口归属和覆盖率统计。

本设计在 Task 10 真实数据导入前统一主数据安全原则，同时保持 Stage 3B0 审核域和旧 ingestion 审核域的生命周期分离。

## 2. 目标

1. 只有唯一的精确主名称或精确别名可以自动关联已有公司。
2. 模糊名称、名称所有权冲突和证据冲突必须进入持久化人工审核队列。
3. 审核完成前，不写入该批次的公司、职位、备案、来源文档或缓存状态。
4. 普通采集永不修改已有公司的 `canonical_name` 或 `normalized_name`。
5. 审核确认关联时保留稳定主名称，把新名称写为别名。
6. 只有显式 `rename_canonical` 决定可以修改主名称；旧主名称必须原子转存为别名。
7. 提供只读历史审计，识别可能已存在的主名称漂移、身份冲突和可疑合并，不自动修复历史数据。
8. PostgreSQL 万级公司场景使用有界候选召回，不在 Python 中逐公司全表扫描。
9. 所有决定、重放和并发冲突可审计、幂等且不泄露敏感数据。

## 3. 非目标

- 不在本阶段自动拆分已经误合并的公司。
- 不根据模糊分数、官网域名、招聘租户、备案号或地区单独自动合并公司。
- 不把旧 ingestion 审核记录塞入 Stage 3B0 `CandidateFact` 审核表。
- 不改变 Task 10 的 1,500 accepted / 1,000 frozen manifest 数量约束。
- 不启动 Task 10 真实来源下载、候选导入或 live discovery。
- 不枚举职位列表，不引入 LLM、浏览器、Redis 或新的外部数据源。

## 4. 核心决策

### 4.1 自动决策边界

自动关联已有公司的充分条件只有：

- 候选规范化主名称唯一精确命中 `companies.normalized_name`；或
- 候选规范化主名称或候选别名唯一精确命中 `company_aliases.normalized_alias`；或
- 候选名称集合通过精确名称/别名图只归属于同一个公司。

以下信息只作为审核证据和冲突信号，不单独触发自动合并：

- 官网 origin 或注册域名；
- 招聘站点租户身份；
- ICP、营业执照、算法备案等标识；
- 城市、地区、集团或品牌关系；
- RapidFuzz 相似度及第一、第二候选分差。

精确名称集合同时指向多个公司时必须审核，不得按查询顺序或 UUID 选取公司。

### 4.2 稳定主名称

- 新公司创建时设置 `canonical_name` 和 `normalized_name`。
- 普通 ingestion 对已有公司只能补充别名和更新非身份资料字段。
- `link_as_alias` 保持主名称不变。
- `rename_canonical` 必须由人工显式提交；事务内先验证新名称所有权，再把旧主名称写入别名表，最后更新主名称。
- 任何跨表名称冲突都使整个决定失败，不允许部分写入。

### 4.3 审核前零业务写入

`NormalizedBatchBuilder` 在构造 `NormalizedBatch` 前完成公司身份解析，并返回冻结的 `BatchBuildOutcome`。解析结果为 `review_required` 时：

1. builder 只生成包含规范化名称、别名、公开证据引用和候选匹配的审核 draft，不打开写事务。
2. orchestrator 重新确认 crawl claim 所有权。
3. 审核服务在独立事务中创建或重放审核项，并绑定 crawl run id。
4. orchestrator 以稳定错误码 `company_identity_review_required` 结束当前 crawl run。
5. 不调用 `PersistenceService.persist()`。
6. 不写公司、别名、职位、备案、来源文档、公司来源关系或缓存。

审核决定应用后，运营人员重新运行采集。下一次解析应通过新建公司或新增别名获得唯一精确匹配，再进入正常持久化流程。

## 5. 模块边界

新增 `app/company_identity/` 域：

```text
backend/app/company_identity/
├── contracts.py       # 冻结 DTO、状态、决定 action、公开报告
├── resolver.py        # 精确解析、模糊候选召回、review_required 判定
├── models.py          # 审核项和 append-only 决定 ORM
├── service.py         # 审核创建、导出、决定应用和事务边界
├── audit.py           # 只读历史身份审计
└── cli.py             # 审核导出/应用和审计命令组合
```

现有模块职责变化：

- `app.ingestion.deduplication.company`：保留兼容入口，但委托新的 resolver；删除模糊自动关联行为。
- `app.ingestion.orchestrator`：消费 `BatchBuildOutcome`；重新确认 claim 后持久化审核项并阻止业务批次写入。
- `app.ingestion.persistence.service`：已有公司身份字段不可由普通采集覆盖；候选名称按规则写入别名。
- `app.ingestion.repositories`：精确所有权查询与 PostgreSQL Top-K 相似候选查询。
- `app.manifest.identity`：维持现有 Stage 3B0 生命周期；共享名称规范化和名称所有权不变量，但不共享审核表。
- `app.manifest.cli`：注册新的 operator 子命令，沿用排序 JSON、稳定退出码和原子文件写入规则。

`NormalizedBatchBuilder` 必须把 discovery `CompanyCandidate.aliases` 规范化后继续传递到 persistence boundary。当前旧路径重新构造 company candidate 时丢弃 aliases，本设计明确移除这一信息损失。

## 6. Resolver 契约

`CompanyIdentityResolver.resolve(...)` 返回冻结的 `CompanyIdentityResolution`：

- `kind`: `existing | new | review_required`
- `company_id`: 仅 `existing` 时有值
- `stable_identity_hash`: 候选公开身份快照的 lowercase SHA-256
- `candidate_matches`: 有界且稳定排序的候选公司证据
- `review_reasons`: 稳定原因代码集合

稳定原因代码至少包括：

- `ambiguous_exact_owner`
- `fuzzy_name_neighbor`
- `short_name_collision`
- `website_identity_conflict`
- `recruitment_identity_conflict`
- `legal_identity_conflict`
- `similarity_search_unavailable`

判定顺序：

1. 重新验证和规范化候选及别名。
2. 查询所有精确名称/别名 owner。
3. owner 恰好为一个时返回 `existing`。
4. owner 多于一个时返回 `review_required`。
5. 无精确 owner 时执行 PostgreSQL 有界相似候选召回并收集冲突证据。
6. 存在任何相似或冲突候选时返回 `review_required`。
7. 没有候选且相似搜索能力可用时返回 `new`。
8. 生产 PostgreSQL 相似搜索不可用时 fail closed，返回 `review_required`，不得返回 `new`。

相似分数只决定审核展示排序，不改变自动决策边界。排序键固定为分数降序、规范化名称升序、公司 UUID 升序。

`NormalizedBatchBuilder.build(...)` 返回冻结的 `BatchBuildOutcome`：

- `ready`：包含 `NormalizedBatch`；
- `review_required`：包含 `CompanyIdentityReviewDraft`，其中只允许规范化公开字段、脱敏证据引用和有界候选匹配。

该 outcome 替代用普通异常携带审核数据的做法。未知异常仍走现有 `ingestion_failed` 兜底；身份审核是预期业务状态，不输出 traceback。

## 7. 数据模型

### 7.1 `company_identity_review_items`

不可变候选快照和创建时匹配证据：

- `id`: UUID 主键
- `stable_identity_hash`: lowercase SHA-256，唯一
- `status`: `pending | resolved | rejected`
- `candidate_name`, `normalized_name`
- `aliases`: 有界 JSON 数组
- `official_website`: 规范化、脱敏公开 URL，可空
- `public_evidence_refs`: 有界 JSON 数组，只含 provider、公开 URL、evidence id 和 confidence
- `candidate_matches`: 有界 JSON 数组，只含公司 ID、公开名称、命中类型、分数和冲突代码
- `review_reasons`: 有界 JSON 数组
- `created_at`
- `resolved_at`: 可空

同一公开身份和证据快照重放返回同一审核项。身份内容发生变化时产生不同 hash 和新审核项，不覆盖旧记录。

### 7.2 `company_identity_review_decisions`

append-only 人工决定：

- `id`: UUID 主键
- `review_item_id`: 外键
- `action`: `link_as_alias | create_new | rename_canonical | reject`
- `target_company_id`: action 要求时非空
- `reason`: 必填、有界、不得包含秘密
- `decided_at`: UTC
- `decision_hash`: canonical sorted JSON 的 lowercase SHA-256
- `resulting_company_id`: 可空

相同 decision hash 重放返回原结果；同一审核项的不同决定触发冲突。决定应用时使用 `FOR UPDATE` 锁定审核项、目标公司和涉及的名称所有权记录，并重新计算当前 owner，不能盲信旧候选快照。

### 7.3 PostgreSQL 相似度索引

迁移启用并验证 `pg_trgm`，为以下字段创建 trigram 索引：

- `companies.normalized_name`
- `company_aliases.normalized_alias`

精确查询继续使用现有唯一 B-tree 索引。模糊查询必须在数据库中排序和限制 Top-K，再对返回的有界集合使用 RapidFuzz 生成确定性展示分数。生产路径不得调用 `list_for_deduplication()` 全表读取。

若部署账号不能创建 extension，环境预检必须在迁移前明确失败；不得静默退回全表扫描或把未知候选视为新公司。

## 8. 人工决定语义

### `link_as_alias`

- 要求有效 `target_company_id`。
- 候选主名称和候选别名逐一检查全局所有权。
- 未被其他公司占用的名称写入目标公司的别名。
- 目标公司的主名称保持不变。

### `create_new`

- 应用时重新确认不存在精确 owner 或新的冲突。
- 创建公司，以审核候选名称作为初始主名称。
- 其余候选名称写入别名。
- 若审核期间名称已被占用，决定失败并保持 pending。

### `rename_canonical`

- 要求有效 `target_company_id`。
- 新主名称不得属于其他公司。
- 旧主名称必须先成为同一公司的别名。
- 与新主名称相同的旧别名被移除或转换，保证跨表所有权唯一。
- 所有变化位于一个事务中。

### `reject`

- 不创建或修改公司身份。
- 将审核项标记为 rejected，并保留 append-only 决定。

## 9. CLI 与操作流程

新增命令：

```text
identity-review-export OUTPUT
identity-review-apply DECISIONS
company-identity-audit OUTPUT
```

共同规则：

- stdout 为一个按 key 排序的 JSON 对象。
- 业务冲突退出 2，环境或数据库失败退出 1。
- stderr 使用稳定、脱敏错误，不输出数据库 URL、凭证、原始响应、决定文件内容或 traceback。
- export 和 audit 使用同目录唯一临时文件加 `Path.replace()`；失败只删除自己的临时文件，永不递归清理目录。
- 工作文件位于 Git 外。

标准操作顺序：

1. ingestion 遇到模糊或冲突身份，创建/重放 pending 审核项并停止该批次。
2. `identity-review-export` 输出稳定排序的公开审核包。
3. 人工根据公开证据选择 action、目标公司和原因。
4. `identity-review-apply` 原子应用决定。
5. 重跑原采集任务，通过精确身份继续持久化。

## 10. 历史只读审计

`company-identity-audit` 不获取外部数据、不修改数据库，输出：

- 主名称与另一公司别名的跨表冲突；
- 高相似主名称/别名簇；
- 相同官网身份被多个公司占用；
- 一家公司关联多个不相容招聘租户；
- 疑似主名称漂移、重复或孤立别名；
- pending 审核项与当前所有权的新冲突。

每条 finding 包含稳定 finding id、severity、涉及的 company ids、公开显示名称、证据代码和建议人工 action。报告包含明确扫描分母和分类计数。所有 URL 去除 query、fragment 和 userinfo；报告不包含原始响应或个人信息。

审计不执行自动拆分、合并、重命名或别名修复。历史修复必须由后续单独批准的人工决定完成。

## 11. 迁移与路线图

数据库当前 head 为 `0008_gate1_manifest_discovery`。本功能占用：

- `0009_company_identity_review`

原 roadmap 中后续迁移顺延为：

- `0010_job_details`
- `0011_coverage_query_indexes`

迁移必须：

- 创建审核项、决定表及约束；
- 创建/验证 `pg_trgm` 和两个 trigram 索引；
- 不修改已有公司身份数据；
- SQLite 与 PostgreSQL upgrade/downgrade 保留所有既有行和外键；
- downgrade 只删除本迁移拥有的对象，不使用 `CASCADE`，不递归删除文件或目录。

`docs/dev/migration-master-plan.md`、`docs/dev/job-coverage-at-scale-plan.md` 和 Stage 3B0 执行计划必须同步记录编号顺延和 Task 10 前置门禁。

## 12. 错误处理与安全

- 审核前停止使用稳定错误码 `company_identity_review_required`。
- 精确 owner 不唯一使用 `company_identity_ambiguous`。
- 决定已被不同命令占用使用 `company_identity_decision_conflict`。
- 名称在审核期间被占用使用 `company_identity_owner_changed`。
- PostgreSQL 相似搜索不可用使用 `company_identity_search_unavailable`。
- 所有公开 DTO 有长度、数量和 extra-field 拒绝规则，并保持冻结。
- 决定 reason 和证据引用经过重新验证；错误消息不回显被拒绝值。
- 不加载模型 API、Zhihu、Redis 或浏览器配置。

## 13. 验证策略

### 单元与集成行为

- 唯一精确主名称和别名自动匹配。
- 精确 owner 歧义、模糊近邻、短名称、同分候选、集团/子公司和域名冲突全部进入审核。
- 官网、招聘租户、备案或地区单独命中不会自动合并。
- `review_required` 前后业务表行数不变，只新增或重放审核项。
- `link_as_alias` 不改变主名称。
- `rename_canonical` 原子保存旧主名称。
- `create_new` 在审核期间出现 owner 时失败。
- `reject` 不创建公司。
- 相同决定重放幂等，不同决定和并发决定冲突。
- 候选 aliases 真正写入并参与后续精确匹配。
- 普通 persistence 不再覆盖已有主名称。

### 审计与安全

- 历史审计结果确定、稳定排序且只读。
- 所有 URL 和错误脱敏。
- 原子文件失败不影响目标文件或其他临时文件。
- 默认测试不访问网络、Redis、LLM 或浏览器。

### 数据库与性能

- SQLite 迁移往返和外键完整性。
- PostgreSQL migration、extension、索引和 downgrade 门禁。
- PostgreSQL `EXPLAIN` 证明相似查询使用 trigram 索引并在数据库内 Top-K。
- 10,000 家合成公司及别名的 performance marker 验证返回集合有界，production resolver 不执行 Python 全表扫描。
- 决定应用使用真实 PostgreSQL 双会话测试验证锁等待、重放和冲突行为。

### 回归门

- company identity focused tests
- ingestion deduplication、orchestrator、persistence tests
- Stage 3B0 manifest、CLI、integration 和 migrations
- provider、coverage 和 API 回归
- Ruff、mypy、tracked-file secret scan、`git diff --check`

## 14. Task 10 解除条件

Task 10 在以下条件全部满足前保持暂停：

1. 本设计的 implementation plan 单独获批并实施。
2. 新旧两条公司身份路径通过独立代码审查和全分支审查。
3. `0009_company_identity_review` 在 SQLite 和本地 PostgreSQL 通过迁移门禁。
4. 历史审计在 Task 10 专用数据库运行，所有 Critical/Important findings 被人工裁定。
5. 旧 ingestion 的模糊自动合并和普通主名称覆盖不存在。
6. 10,000 家性能门禁和完整离线回归通过。
7. 工作区无密钥、原始候选下载或审核工作文件进入 Git。

解除后，Task 10 仍按既有顺序执行：注册来源、导入外部候选、清空审核队列、冻结 1,000 家 manifest、20 家 live smoke、再 resume 剩余成员。公司身份审核门禁不扩大 Task 10 到职位列表枚举。
