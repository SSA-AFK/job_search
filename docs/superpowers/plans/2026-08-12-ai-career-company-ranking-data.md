# AI 求职公司榜精简数据实施计划

> 本计划实现已批准的 `2026-08-12-ai-career-company-ranking-data-design.md`。所有任务按测试先行执行；每项完成后只提交该任务涉及的文件，不混入现有工作区的无关改动。

**目标：** 对既有 AI 行业 100 家确定性样本，以 Excel 内部基线、每家公司按缺口最多四类天眼 AI 调用和独立公开证据，形成面向 AI 求职者的长期职业价值内部校准榜。

**架构：** 将 Excel 作为首批样本的内部基线缓存，将天眼 AI 视为仅内部使用的增量结构化供应商边界。适配器通过字段白名单把最多四类响应投影成最小化事件，不保存原始响应；官网、政府及审核后的行业组织来源继续提供用户可见证据。评分服务先生成五个维度的原始证据分，再按企业发展阶段校准并形成版本化快照。首批结果仅通过内部 CLI/报告访问。

**技术栈：** Python 3.12、SQLAlchemy、Alembic、Pydantic v2、天眼 AI CLI/MCP、SQLite 隔离试点库、pytest、ruff、mypy。

## 全局约束

- 保持当前工作簿、种子和 100 家成员不变；数据补充不得改变样本。
- 只对显式传入的隔离 SQLite 数据库执行导入、采集和重评分。
- Excel 已有的工商、阶段和行业字段不得重复查询；每家公司按缺口最多执行四个数据类别、六个底层工具调用。
- 实体搜索只在企业全称与信用代码不能唯一锚定时执行，并单独计入运行审计，不计入四类评分数据调用。
- 不把天眼评分、原始响应、供应商 URL、供应商字段名或供应商身份暴露给公共 API。
- 不采集电话、邮箱、完整地址、法定代表人、董监高个人履历、个人关系图谱、司法全文和股东穿透图。
- API 响应先白名单投影，后持久化；原始 payload 仅在内存中存在，日志不得序列化响应。
- 缺失等于未知，不等于负面；没有证据的项目不加分也不扣分。
- 招聘职位、招聘趋势、薪资数据不进入首版采集与评分。
- 保留现有官网 robots、域名白名单、身份校验和内容去重边界。

## 目标文件结构

| 文件 | 职责 |
| --- | --- |
| `backend/app/rankings/tianyancha/contracts.py` | 四类增量调用的最小输入、白名单输出和错误契约。 |
| `backend/app/rankings/tianyancha/client.py` | 天眼 CLI 的确定性执行、认证、超时、重试及预算控制。 |
| `backend/app/rankings/tianyancha/catalog.py` | 运行时能力发现及四类语义能力映射。 |
| `backend/app/rankings/tianyancha/projectors.py` | 将供应商响应投影成阶段、技术、市场、成长、地位和风险信号。 |
| `backend/app/rankings/tianyancha/service.py` | 对试点成员执行缺口判断、去重、断点续跑和最多四类调用。 |
| `backend/app/rankings/staging.py` | 企业发展阶段判定及小样本阶段合并。 |
| `backend/app/rankings/scoring.py` | 原始证据分、时效衰减、阶段百分位和最终五维评分。 |
| `backend/app/rankings/models.py` | 供应商运行审计、最小化信号和扩展评分快照。 |
| `backend/alembic/versions/0019_ai_ranking_signals.py` | 新增审计、信号和阶段校准字段。 |
| `backend/app/rankings/cli.py` | `collect-tyc`、`enrich-official`、`score`、`report` 运维入口。 |
| `backend/tests/rankings/` | 契约、隐私、调用预算、阶段和评分验收测试。 |

## Task 1：冻结现有 100 家基线并验证数据库边界

**文件：**

- 修改：`backend/tests/rankings/test_selection.py`
- 修改：`backend/tests/rankings/test_service.py`
- 新建：`backend/tests/rankings/test_pilot_acceptance.py`

- [ ] 编写基线测试，验证工作簿即使声明维度为 `A1`，仍读取 34 列、4,366 条企业记录。
- [ ] 验证仅“存续/在业”记录参与去重候选池，固定工作簿与种子稳定选出相同 100 家。
- [ ] 验证改变种子会改变成员，而省份 × 国标行业大类 × 天眼评分五分位的 Hamilton 配额总和恒为 100。
- [ ] 固化当前 100 家的有序身份哈希摘要作为回归夹具，不把公司原始信用代码写入夹具。
- [ ] 检查隔离数据库保护：CLI 拒绝默认数据库、非 SQLite URL 和未显式提供的数据库路径。
- [ ] 运行：`pytest tests/rankings/test_selection.py tests/rankings/test_service.py tests/rankings/test_pilot_acceptance.py -q`。

**验收：** 样本边界字节级稳定，且测试或日志不含信用代码原文和被排除字段。

## Task 2：建立最小化信号与采集审计模型

**文件：**

- 修改：`backend/app/rankings/models.py`
- 修改：`backend/app/models/__init__.py`
- 新建：`backend/alembic/versions/0019_ai_ranking_signals.py`
- 新建：`backend/tests/rankings/test_models.py`
- 修改：`backend/tests/migrations/test_migrations.py`（若仓库通过此文件维护期望表集合）

- [ ] 先写失败测试，要求每条结构化信号包含 `company_id`、`category`、`signal_key`、白名单 `value`、`event_date`、`fetched_at`、`expires_at`、响应哈希、置信度、核验状态和证据引用。
- [ ] 新增 `ranking_collection_runs`：记录 pilot、公司、能力类别、状态、开始/结束时间、调用次数、错误码和响应哈希；不保存命令 stdout/stderr 原文。
- [ ] 新增 `company_ranking_signals`：以 `(company_id, category, signal_key, source_fingerprint)` 去重，值仅允许标量或受限结构。
- [ ] 扩展快照字段：`company_stage`、`raw_component_scores`、`stage_percentiles`、`evidence_coverage` 和 `eligibility_reasons`。
- [ ] 为信号类型建立枚举：`company_anchor`、`growth_event`、`ai_patent`、`ai_software_copyright`、`market_proof`、`qualification`、`risk_event`、`stage_event`。
- [ ] 在模型测试中枚举数据库列，断言不存在 `phone`、`email`、`address`、`legal_person`、`executive`、`raw_payload`、`vendor_score`。
- [ ] 验证迁移 upgrade/downgrade，并保证只删除精确命名的新表；不得递归删除目录或数据库文件。

**验收：** 可以审计四类调用和评分输入，但无法从持久化结构恢复供应商原始响应或个人敏感信息。

## Task 3：实现天眼四类增量调用客户端与能力映射

**文件：**

- 新建：`backend/app/rankings/tianyancha/__init__.py`
- 新建：`backend/app/rankings/tianyancha/contracts.py`
- 新建：`backend/app/rankings/tianyancha/catalog.py`
- 新建：`backend/app/rankings/tianyancha/client.py`
- 修改：`backend/app/core/config.py`
- 新建：`backend/tests/rankings/tianyancha/test_client.py`
- 新建：`backend/tests/rankings/tianyancha/test_catalog.py`

- [ ] 通过已安装 CLI 的 `layers/list` 能力发现结果确认真实工具名，禁止在未验证前猜测命令名称。
- [ ] 将真实工具稳定映射到四个内部能力：融资成长、知识产权、经营验证、经营风险。
- [ ] 客户端使用参数数组启动子进程，不拼接 shell 字符串；Windows 包装器也不得把公司名直接插入 shell 命令。
- [ ] 配置增加 CLI 路径、超时、每公司最大四类、批次并发、重试次数；API Key 只从天眼 CLI 本地配置或环境密钥读取。
- [ ] 对认证失败、限流、额度不足、超时、非 JSON、超大响应和未知能力定义稳定错误码。
- [ ] 仅对限流和临时网络错误指数退避；实体歧义、权限不足和契约错误不得自动反复调用。
- [ ] 测试证明同一能力重试不会突破业务调用预算，且 stdout、stderr 和异常信息不会进入普通日志。
- [ ] 明确弃用现有通用 `TianyanchaProvider` 作为排名数据入口；排名模块不得把供应商 JSON包装成 `RawDocument.text` 落库。

**验收：** 能确定性调用已验证的四类能力，单家公司正常数据类别不超过四类、底层工具调用不超过六次，且供应商密钥与原始响应不落库、不进日志。

## Task 4：实现字段白名单投影与 AI 相关过滤

**文件：**

- 新建：`backend/app/rankings/tianyancha/projectors.py`
- 新建：`backend/app/rankings/tianyancha/ai_taxonomy.py`
- 新建：`backend/tests/rankings/tianyancha/test_projectors.py`
- 修改：`backend/app/profiles/catalog.py`

- [ ] 为四类响应分别写契约夹具，夹具同时包含允许字段和电话、人员、地址等禁止字段。
- [ ] 融资成长只投影近三年的融资轮次、日期、产业资本进入、增资、并购和上市事件；不保存完整股东表。
- [ ] 知识产权只投影近三年 AI 相关发明专利及软件著作权的日期、类型和脱敏标题摘要；本地分类规则版本化。
- [ ] 经营验证只投影近三年 AI 相关中标、资质和重要公告；金额只用于单条证据判断，不作为跨公司绝对量加分。
- [ ] 风险只投影经营异常、严重违法失信、重大处罚、执行、失信执行和破产清算的严重度、日期及解除状态。
- [ ] 扩展画像目录为具体字段，例如 `ai.patents_3y`、`ai.software_copyrights_3y`、`market.public_proofs_3y`、`growth.material_events_3y`、`industry.qualifications`、`risk.material_events`、`organization.stage`。
- [ ] 投影后立即丢弃原始对象；测试递归扫描投影结果，禁止字段无论嵌套层级都不得出现。

**验收：** 四类响应均能生成最小化、可版本化的内部信号；供应商新增未知字段默认被丢弃。

## Task 5：实现断点续跑的 100 家批量采集

**文件：**

- 新建：`backend/app/rankings/tianyancha/service.py`
- 修改：`backend/app/rankings/cli.py`
- 新建：`backend/tests/rankings/tianyancha/test_service.py`
- 新建：`backend/tests/rankings/test_cli.py`

- [ ] 增加 `collect-tyc` 子命令，参数包含显式数据库 URL、pilot ID、能力选择、并发和 `--resume`。
- [ ] 用企业全称或现有不可逆身份标识锚定；若供应商返回多个实体，记录 `identity_review_required`，停止该公司的后续调用。
- [ ] 为每个公司与能力建立幂等运行键；成功且未过期的能力不重复请求，失败项可按错误类型恢复。
- [ ] 根据 Excel 基线和未过期缓存生成缺口计划；企业综合画像、基础工商和完整历史永不进入调用计划。
- [ ] 在调度前和每次调用前双重检查四类预算；批量接口减少往返但仍按返回公司数记录逻辑调用。
- [ ] 每家公司完成后立即提交最小化信号和运行状态，进程中断时已完成公司不回滚。
- [ ] CLI 进度只输出数量和稳定错误码：总数、已完成、待复核、失败、跳过；不输出公司原始响应。
- [ ] 测试 100 家 × 四类的上界为 400 个逻辑类别、600 个底层工具调用；已有成功数据、重复运行和不适用维度都会减少实际调用。
- [ ] 测试认证失败时批次快速停止，单个公司数据问题只隔离该公司。

**验收：** 可安全运行、停止和恢复首批 100 家采集；调用次数和失败原因完整可审计。

## Task 6：补充官网与独立公开证据

**文件：**

- 修改：`backend/app/enrichment/official.py`
- 修改：`backend/app/rankings/official_profile.py`
- 修改：`backend/app/rankings/service.py`
- 修改：`backend/tests/rankings/test_official_profile.py`
- 新建：`backend/tests/rankings/test_public_evidence.py`

- [ ] 将官网补充范围收敛为三类：AI 是否核心业务、代表性 AI 产品、代表性客户案例。
- [ ] 只有白名单官网、政府或已审核行业组织来源可生成用户可见字段；天眼信号只能作为内部检索线索。
- [ ] 修正关键词命中策略：单独出现通用 `AI`、产品、平台、合作等词不得自动形成 verified 结论；要求公司身份一致并满足字段专用规则。
- [ ] 将自动提取结果先标记为待核验；仅确定性页面类型或人工审核后升级为 verified。
- [ ] 每个用户可见字段保存来源 URL、发布时间、获取时间、内容哈希和核验状态。
- [ ] 继续执行 robots 失败关闭、同域限制、页面上限、超时和内容哈希去重。
- [ ] 测试天眼原始字段或供应商页面不能成为用户可见证据链接。

**验收：** 三个核心求职字段均来自允许公开展示的独立证据；弱关键词不会制造虚假 AI 画像。

## Task 7：实现发展阶段与阶段校准评分

**文件：**

- 新建：`backend/app/rankings/staging.py`
- 新建：`backend/app/rankings/scoring.py`
- 修改：`backend/app/rankings/service.py`
- 新建：`backend/tests/rankings/test_staging.py`
- 新建：`backend/tests/rankings/test_scoring.py`

- [ ] 定义可解释的阶段规则，只使用成立时间、融资/上市状态和规模分档，输出 `early`、`growth`、`mature` 及规则版本。
- [ ] 设定阶段最小样本量；不足时按固定邻接规则合并，并把合并理由写入快照。
- [ ] 定义五个原始维度：AI 核心与产品 30、市场验证 25、成长动能 20、行业地位 15、稳定性与透明度 10。
- [ ] 原始分只从已核验证据计算；累计规模指标先转近三年新增或阶段内相对量。
- [ ] 成长与市场事件采用三年窗口；资质按有效期；风险按严重度、发生时间和解除状态衰减。
- [ ] 在同阶段内计算确定性的 mid-rank 百分位；同分公司获得相同百分位，最终排序以公司规范名和 ID 稳定打破展示顺序。
- [ ] 设定正式排名资格：AI 核心性必须有独立公开证据，且五维中至少四维达到最低证据覆盖；否则进入内部观察池。
- [ ] 快照关联所有实际影响分数的证据；已过期、低置信度、待核验信号不参与得分。
- [ ] 测试成熟公司不会仅凭累计专利/资本规模压过早期公司；测试缺失不产生负分、严重且未解除风险会扣分。

**验收：** 固定输入产生稳定总分和排名；所有分值都能反查证据、原始分、阶段百分位和规则版本。

## Task 8：提供仅内部的校准报告与隐私防线

**文件：**

- 修改：`backend/app/rankings/cli.py`
- 修改：`backend/app/rankings/service.py`
- 新建：`backend/app/rankings/reporting.py`
- 新建：`backend/tests/rankings/test_reporting.py`
- 修改：`backend/tests/api/test_companies.py`

- [ ] 将运维命令拆成 `import`、`collect-tyc`、`enrich-official`、`score` 和 `report`，每步可独立恢复。
- [ ] 内部报告包含公司、阶段、五维分、总分、资格、缺失字段、证据覆盖、失败类别和规则版本。
- [ ] 报告不包含源行号、供应商企业 ID、信用代码哈希、原始工商状态、供应商名称或原始响应。
- [ ] 若运营需要查看匹配/采集审计，通过单独 `audit` 输出稳定 ID 与错误码，不与榜单报告混合。
- [ ] 公共公司搜索、详情和职位 API 递归扫描不得出现 `ranking` 内部字段、天眼字段、供应商标识或采集错误。
- [ ] 日志测试捕获成功、失败、异常和重试路径，验证禁止字段与 API Key 均未泄露。

**验收：** 内部人员可以校准评分和定位缺失；公共产品仍无法观察试点、供应商或内部中间值。

## Task 9：执行首批 100 家与最终验收

**文件：**

- 修改：`backend/README.md` 或根目录 `README.md`（按现有运维文档位置）
- 新建：`backend/tests/integration/test_ai_ranking_pilot.py`

- [ ] 在显式隔离 SQLite 数据库上升级迁移并导入固定 100 家，记录 pilot ID 和样本摘要。
- [ ] 完成天眼认证后先对 3 家跨阶段样本运行四类调用，人工检查投影字段与禁止字段。
- [ ] 通过冒烟检查后运行 100 家批次；允许断点续跑，禁止为追求覆盖率绕过实体歧义和契约错误。
- [ ] 运行官网/公开证据补充、评分和内部报告，统计正式排名、观察池、字段覆盖和失败分布。
- [ ] 对最高分、最低分、阶段边界、风险扣分和证据稀疏样本各抽查至少 3 家。
- [ ] 执行聚焦测试：`pytest tests/rankings tests/ingestion/providers/test_tianyancha.py tests/api/test_companies.py -q`。
- [ ] 执行质量检查：`ruff check app/rankings app/ingestion/providers/tianyancha.py tests/rankings`、`mypy app/rankings`。
- [ ] 执行公共 API 回归和现有公司/职位关键流程；若全量迁移套件存在既有失败，单独记录并证明新增迁移自身 upgrade/downgrade 通过。
- [ ] 文档记录运行命令、四类能力映射版本、评分规则版本、数据更新时间和人工复核清单；不得写入 API Key。

**最终验收：** 固定 100 家样本可重复生成内部校准榜；单家公司不超过四类天眼数据、六个底层工具调用；每个有效得分都有证据；敏感与供应商内部字段不落库、不进日志、不出公共 API；现有公司搜索、详情和职位流程无回归。

### 2026-08-12 执行记录

- 隔离库：`backend/company_ranking_pilot_v2.sqlite3`；pilot ID：`f5becbff-f425-45ac-9922-27fa377fbf0e`。
- 输入 4,366 条企业记录，规范化后 4,317 条候选，固定种子选取 100 家；阶段分布为早期 5、成长期 53、成熟期 42。
- `tyc-ranking-v2` 完成 400/400 个数据类别、0 失败、600 个底层工具调用；重复运行 400 个类别全部跳过且调用为 0。
- 投影后共有 1,495 条最小化信号：成长 19、知识产权 134、市场验证 1,309、重大风险 33；未保存原始响应。
- 官网补充成功 16 家、无官网 37 家、关闭失败 47 家；33 个画像提示全部为待核验，未产生公开加分。
- 五维中市场验证、成长动能、行业影响力、可靠性覆盖均为 100 家；已核验 AI 核心性为 0 家，因此正式公开资格为 0，100 家均保留在内部观察池。
- 敏感字段和供应商标识扫描 0 命中；排名、天眼适配器和公司公共 API 聚焦回归 53 项通过，Ruff 与 mypy 通过。
- 后端全量回归为 1,179 通过、12 跳过、17 失败；失败集中在工作区既有职位采集重试计数、旧迁移链和 `JobEntry` 默认值，不涉及排名专项。旧迁移链在 `0010_entry_evidence_rounds` 的 SQLite 外键不匹配处仍需由原模块单独修复。

### 2026-08-12 自动化规则修订

- 官网退出默认采集和评分路径，仅作为前 20 家或争议公司的可选复核来源；缺官网不扣分、不阻塞入榜。
- `ai-long-term-v2` 以经营范围严格 AI 术语命中或近三年 AI 专利/软著判定 AI 相关性；经营范围原文不落库，仅保存派生分类、命中数量和不可逆指纹。
- 阶段校准增加“原始分为 0 则分项恒为 0”的硬约束，确保无证据不因并列百分位获得分数。
- 现有 100 家本地重评分后，98 家正式入榜、2 家观察；分数范围 8–86。观察公司为北京凯芯微科技有限公司、北京彼岸京华教育科技有限公司。
- 聚焦回归更新为 57 项通过，Ruff 与 mypy 通过；重评分不触发官网或天眼网络调用。

## 建议提交顺序

1. `test: freeze AI ranking pilot baseline`
2. `feat: add minimized ranking signal records`
3. `feat: add bounded Tianyancha ranking client`
4. `feat: project Tianyancha ranking signals`
5. `feat: collect ranking signals with resume support`
6. `fix: tighten official AI evidence rules`
7. `feat: add stage-calibrated AI career scoring`
8. `feat: add internal ranking calibration report`
9. `docs: document AI ranking pilot operations`
