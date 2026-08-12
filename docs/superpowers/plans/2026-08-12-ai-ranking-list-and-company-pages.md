# AI 榜单与公司页面实施计划

**目标：** 在同一业务数据库中发布固定 AI pilot，以 `/list` 展示统一榜单、以 `/companies` 展示固定 100 家目录，并扩展详情页展示公开公司与评分信息；职位仅保留占位。

**设计依据：** `docs/superpowers/specs/2026-08-12-ai-ranking-list-and-company-pages-design.md`

## Task 1：冻结公共榜单契约与排序

**文件：**

- 新建：`backend/app/rankings/schemas.py`
- 新建：`backend/app/rankings/repository.py`
- 新建：`backend/tests/rankings/test_public_repository.py`

- [ ] 定义榜单摘要、榜单成员、评分分项、详情评分和公开信号模型。
- [ ] 查询当前 `ai-long-term-v2` 快照，正式成员与观察成员分离。
- [ ] 正式名次按总分、AI 核心性、市场验证、成长动能、行业影响力、可靠性、公司名确定性排序。
- [ ] 公开信号只映射白名单字段，不返回供应商、内部哈希、源行号和运行信息。
- [ ] 测试连续名次、观察池无名次、稳定排序和敏感字段递归扫描。

## Task 2：新增榜单 API

**文件：**

- 新建：`backend/app/rankings/router.py`
- 新建：`backend/app/rankings/public_service.py`
- 修改：`backend/app/api/router.py`
- 新建：`backend/tests/api/test_rankings.py`

- [ ] 实现 `GET /api/v1/rankings/ai`，支持 `status`、`stage` 和分页。
- [ ] 返回规则版本、更新时间、正式/观察总数和成员。
- [ ] 无已发布 pilot 时返回稳定错误，不退回任意公司数据。
- [ ] 验证 API 不触发天眼或官网网络调用。

## Task 3：把公司目录限定为当前 100 家

**文件：**

- 修改：`backend/app/companies/repository.py`
- 修改：`backend/app/companies/service.py`
- 修改：`backend/app/companies/schemas.py`
- 修改：`backend/tests/api/test_companies.py`

- [ ] 公司搜索查询连接当前 AI pilot 成员和 v2 快照。
- [ ] 默认按正式榜单名次排序，观察成员置后；搜索和筛选仅作用于 100 家。
- [ ] 列表响应增加 `ranking_status`、`rank`、`ranking_score` 和 `company_stage`。
- [ ] 移除空结果自动采集的后端依赖，不更改职位代码。
- [ ] 非当前成员详情返回 404，即使底层 `companies` 表存在。

## Task 4：扩展公司详情公共信息

**文件：**

- 修改：`backend/app/companies/repository.py`
- 修改：`backend/app/companies/service.py`
- 修改：`backend/app/companies/schemas.py`
- 修改：`backend/tests/api/test_companies.py`

- [ ] 详情增加总分、分项、阶段、名次、规则版本、计算时间、缺失原因和公开评分信号。
- [ ] 映射融资、AI 专利/软著、中标/资质和重大风险的白名单字段。
- [ ] 不返回信用代码、联系方式、完整地址、供应商标识、响应哈希和内部审计数据。
- [ ] 缺失分项返回 0 与明确说明，不使详情失败。

## Task 5：实现 `/list` 页面

**文件：**

- 新建：`frontend/src/ranking/RankingListPage.tsx`
- 新建：`frontend/src/ranking/RankingListPage.test.tsx`
- 修改：`frontend/src/api/types.ts`
- 修改：`frontend/src/api/client.ts`
- 修改：`frontend/src/app/router.tsx`
- 修改：`frontend/src/styles.css`

- [ ] 顶部展示榜单定位、规则版本、更新时间和固定样本说明。
- [ ] 展示正式名次、阶段、总分、五维评分和核心理由。
- [ ] 支持发展阶段筛选，观察区独立展示且无伪名次。
- [ ] 增加榜单/目录导航，默认及未知路由进入 `/list`。
- [ ] 覆盖加载、错误、空榜单、观察池、移动端和键盘可访问性。

## Task 6：调整 `/companies` 目录

**文件：**

- 修改：`frontend/src/search/SearchPage.tsx`
- 修改：`frontend/src/search/CompanyResults.tsx`
- 修改：`frontend/src/search/SearchPage.test.tsx`
- 修改：`frontend/src/styles.css`

- [ ] 页面文案改为固定 100 家 AI 公司目录。
- [ ] 公司行展示榜单名次或观察状态、总分和阶段。
- [ ] 移除无结果自动采集及轮询入口，不删除底层采集模块。
- [ ] 保留现有名称和结构化筛选、分页与错误重试。

## Task 7：重构详情页并保留职位占位

**文件：**

- 修改：`frontend/src/company/CompanyDetailPage.tsx`
- 修改：`frontend/src/company/CompanyDetailPage.test.tsx`
- 修改：`frontend/src/styles.css`

- [ ] 首屏展示名次/观察状态、阶段、总分和五维分项。
- [ ] 展示公司概览、AI 线索、市场验证、成长、知识产权、风险和证据。
- [ ] 删除信用代码展示与所有职位 API 请求。
- [ ] 页面底部展示稳定的“职位功能即将开放”占位。
- [ ] 待补充字段保持可见，不输出原始 JSON 字段名。

## Task 8：正式库发布与验收

**文件：**

- 新建：`backend/app/rankings/publish_cli.py`
- 新建：`backend/tests/rankings/test_publish.py`
- 修改：`README.md`

- [ ] 实现从显式校准库到显式目标库的幂等发布命令，禁止默认隐式覆盖。
- [ ] 发布前核验工作簿哈希、固定 100 家、规则版本和敏感字段边界。
- [ ] 整批事务导入公司、成员、最小化信号和快照；失败全回滚。
- [ ] 用当前 100 家数据验证 98 家正式、2 家观察和连续名次。
- [ ] 运行后端聚焦测试、前端单测、TypeScript 构建、Ruff、mypy 和桌面/移动端页面验收。

## 完成标准

- `/list`、`/companies` 和 100 家详情共享同一数据库与公司 ID。
- 榜单和目录不依赖官网、天眼实时调用或职位接口。
- 98 家正式名次与 2 家观察状态稳定可复现。
- 用户侧只展示允许公开的数据；敏感、供应商和内部审计字段扫描为零。
- 职位功能位置已保留，但本轮不采集、不请求、不展示职位数据。
