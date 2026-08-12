import { ArrowLeft, ChevronDown, ExternalLink, RotateCw } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, ApiError } from "../api/client";
import { safeHttpUrl } from "../api/http-url";
import type { CompanyDetail, CompanyScale, FundingStage, RankingComponents, VerificationStatus } from "../api/types";

const fundingLabels: Record<FundingStage, string> = {
  seed: "种子轮",
  angel: "天使轮",
  pre_a: "Pre-A 轮",
  series_a: "A 轮",
  series_b: "B 轮",
  series_c_plus: "C 轮及以后",
  public: "已上市",
  unfunded: "未融资",
  unknown: "未知",
};

const signalGroupLabels: Record<string, string> = {
  ai_relevance: "AI 核心业务",
  growth: "融资与成长",
  intellectual_property: "专利与软著",
  market_validation: "市场验证",
  material_risk: "经营风险",
};

const scaleLabels: Record<CompanyScale, string> = {
  one_to_49: "1-49 人",
  "50_to_199": "50-199 人",
  "200_to_499": "200-499 人",
  "500_plus": "500 人以上",
  unknown: "未知",
};

const filingLabels: Record<string, string> = {
  icp: "ICP备案",
  algorithm: "算法备案",
  business_license: "企业登记",
};

const providerLabels: Record<string, string> = {
  official_registry: "官方登记",
  company_site: "公司官网",
};

const verificationLabels: Record<VerificationStatus, string> = {
  verified: "已核验",
  pending_verification: "待核验",
};

function VerificationBadge({ status }: { status: VerificationStatus | undefined }) {
  const resolvedStatus = status ?? "pending_verification";
  return <span className={`verification-badge verification-badge--${resolvedStatus}`}>{verificationLabels[resolvedStatus]}</span>;
}

function pendingVerificationFields(fieldVerification: Record<string, VerificationStatus> | undefined) {
  return Object.entries(fieldVerification ?? {})
    .filter(([, status]) => status === "pending_verification")
    .map(([field]) => field);
}

function profileFieldValue(value: unknown) {
  if (value === null) return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function CompanyLogo({ company }: { company: CompanyDetail }) {
  const [failed, setFailed] = useState(false);
  const logoUrl = safeHttpUrl(company.logo_url);
  if (logoUrl && !failed) {
    return <img className="detail-logo" src={logoUrl} alt="" width="64" height="64" onError={() => setFailed(true)} />;
  }
  return <span className="detail-logo logo-fallback" aria-hidden="true">{company.canonical_name.trim().slice(0, 2).toUpperCase()}</span>;
}

function ExternalTextLink({ href, children }: { href: string | null; children: React.ReactNode }) {
  const safeHref = safeHttpUrl(href);
  if (!safeHref) return <span>{children}</span>;
  return <a href={safeHref} target="_blank" rel="noreferrer">{children}<ExternalLink aria-hidden="true" size={14} /></a>;
}

const scoreLabels: Array<[keyof RankingComponents, string, number]> = [
  ["ai_core", "AI 核心性", 30], ["market_validation", "市场验证", 25],
  ["growth_momentum", "成长动能", 20], ["industry_influence", "行业影响力", 15],
  ["reliability", "可靠性", 10],
];

function RankingPanel({ company }: { company: CompanyDetail }) {
  const scores = company.ranking_components ?? { ai_core: 0, market_validation: 0, growth_momentum: 0, industry_influence: 0, reliability: 0 };
  return <section className="detail-ranking" aria-label="公司榜单评分"><div className="detail-rank-number"><strong>{company.ranking_score ?? 0}</strong><span>总分</span></div><div><div className="detail-ranking-meta"><strong>{company.rank ? `榜单第 ${company.rank} 名` : "观察中"}</strong><span>{company.company_stage === "early" ? "早期" : company.company_stage === "mature" ? "成熟" : "成长"}阶段</span></div><p>{company.ranking_reason ?? "评分依据待补充"}</p><div className="detail-score-grid">{scoreLabels.map(([key, label, maximum]) => <div key={key}><span>{label}</span><i><b style={{ width: `${scores[key] / maximum * 100}%` }} /></i><strong>{scores[key]}</strong></div>)}</div></div></section>;
}

function signalText(signal: NonNullable<CompanyDetail["ranking_signals"]>[number]) {
  const labels: Record<string, string> = { ai_business_scope: "经营范围明确包含 AI 业务", financing: "融资事件", ai_invention_patent: "AI 发明专利", ai_software_copyright: "AI 软件著作权", winning_bid: "公开中标", active_qualification: "有效资质", material_risk: "重大经营风险" };
  const title = typeof signal.value.title === "string" ? signal.value.title : typeof signal.value.name === "string" ? signal.value.name : "";
  return [labels[signal.signal_key] ?? signal.signal_key, title].filter(Boolean).join("：");
}

function CollapsibleList<T>({ items, limit, className, itemKey, renderItem }: {
  items: T[];
  limit: number;
  className: string;
  itemKey: (item: T, index: number) => string;
  renderItem: (item: T, index: number) => React.ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? items : items.slice(0, limit);
  return <><ul className={className}>{visible.map((item, index) => <li key={itemKey(item, index)}>{renderItem(item, index)}</li>)}</ul>{items.length > limit ? <button className="expand-button" type="button" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? "收起" : `展开全部 ${items.length} 条`}<ChevronDown aria-hidden="true" size={15} /></button> : null}</>;
}

function ExpandableText({ children }: { children: string }) {
  const [expanded, setExpanded] = useState(false);
  return <div className="expandable-copy"><p className={expanded ? "" : "is-clamped"}>{children}</p><button className="expand-button" type="button" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? "收起" : "展开全文"}<ChevronDown aria-hidden="true" size={15} /></button></div>;
}

function DetailContent({ company }: { company: CompanyDetail }) {
  const website = safeHttpUrl(company.website);
  const aliases = company.aliases ?? [];
  const filings = company.filings ?? [];
  const profileFields = company.profile_fields ?? [];
  const sources = company.sources ?? [];
  const signals = company.ranking_signals ?? [];
  const signalGroups = Object.entries(signals.reduce<Record<string, typeof signals>>((groups, signal) => {
    (groups[signal.category] ??= []).push(signal);
    return groups;
  }, {}));
  return (
    <>
      <div className="detail-identity">
        <CompanyLogo company={company} />
        <div>
          <h1>{company.canonical_name}</h1>
          <p className="detail-tags">
            {[company.industry, company.sub_industry, company.latest_funding_round ?? "融资暂未查到", scaleLabels[company.scale], company.city]
              .filter(Boolean).join(" · ")}
          </p>
        </div>
        {website ? <a className="secondary-button detail-website" href={website} target="_blank" rel="noreferrer">公司官网<ExternalLink aria-hidden="true" size={15} /></a> : null}
      </div>
      <RankingPanel company={company} />
      {company.description ? <p className="detail-description">{company.description}</p> : null}
      <dl className="company-facts">
        <div><dt>别名</dt><dd>{aliases.length ? aliases.join("、") : "暂无别名"}</dd></div>
        <div><dt>榜单状态</dt><dd>{company.rank ? `第 ${company.rank} 名` : "观察中"}</dd></div>
        <div><dt>评分规则</dt><dd>{company.ranking_rule_version ?? "待补充"}</dd></div>
        <div><dt>资料更新</dt><dd>{company.updated_at.slice(0, 10)}</dd></div>
      </dl>

      <section className="detail-section" aria-labelledby="profile-heading">
        <h2 id="profile-heading">基本信息</h2>
        <dl className="company-profile">
          <div><dt>所属行业</dt><dd>{[company.industry, company.sub_industry].filter(Boolean).join(" · ") || "待补充"}</dd></div>
          <div><dt>最新融资</dt><dd>{company.latest_funding_round ?? "暂未查到"}</dd></div>
          <div><dt>团队规模</dt><dd>{scaleLabels[company.scale]}</dd></div>
          <div><dt>参保人数</dt><dd>{company.insured_employee_count !== null ? `${company.insured_employee_count} 人${company.employee_report_year ? `（${company.employee_report_year} 年报）` : ""}` : "待补充"}</dd></div>
          <div><dt>主要城市</dt><dd>{company.city ?? "待补充"}</dd></div>
          <div><dt>企业类型</dt><dd>{company.company_type ?? "待补充"}</dd></div>
          <div><dt>成立时间</dt><dd>{company.established_at ?? (company.founded_year ? `${company.founded_year} 年` : "待补充")}</dd></div>
          <div><dt>注册资本</dt><dd>{company.registered_capital ?? "待补充"}</dd></div>
          <div><dt>公司官网</dt><dd>{website ? <a href={website} target="_blank" rel="noreferrer">访问官网<ExternalLink aria-hidden="true" size={14} /></a> : "待补充"}</dd></div>
        </dl>
        <details className="more-profile"><summary>更多工商信息</summary><dl className="company-profile"><div><dt>总部地区</dt><dd>{company.headquarters ?? "待补充"}</dd></div><div><dt>所属区县</dt><dd>{company.district ?? "待补充"}</dd></div><div><dt>实缴资本</dt><dd>{company.paid_in_capital ?? "待补充"}</dd></div><div><dt>国标行业</dt><dd>{[company.industry_sector, company.sub_industry, company.industry_middle].filter(Boolean).join(" · ") || "待补充"}</dd></div></dl></details>
      </section>

      {company.business_scope ? <section className="detail-section" aria-labelledby="scope-heading"><h2 id="scope-heading">经营范围</h2><ExpandableText>{company.business_scope}</ExpandableText></section> : null}

      {company.funding_events.length ? <section className="detail-section" aria-labelledby="funding-heading"><h2 id="funding-heading">融资记录</h2><CollapsibleList items={company.funding_events} limit={3} className="record-list" itemKey={(event, index) => `${event.round_label}:${event.announced_at ?? index}`} renderItem={event => <><div><strong>{event.round_label}</strong><VerificationBadge status={event.verification_status} /></div><span>{event.announced_at ?? "日期暂未披露"}</span><p>{event.investors.length ? `投资方：${event.investors.join("、")}` : "投资方暂未披露"}</p></>} /></section> : null}

      <section className="detail-section" aria-labelledby="signals-heading"><h2 id="signals-heading">评分依据</h2>{signalGroups.length ? <div className="signal-groups">{signalGroups.map(([category, group]) => <section key={category}><h3>{signalGroupLabels[category] ?? category}<span>{group?.length ?? 0} 条</span></h3><CollapsibleList items={group ?? []} limit={3} className="signal-list" itemKey={(signal, index) => `${signal.signal_key}:${signal.event_date ?? index}`} renderItem={signal => <><strong>{signalText(signal)}</strong><span>{signal.event_date ?? "当前有效"}</span></>} /></section>)}</div> : <p className="section-empty">评分依据待补充</p>}</section>

      <section className="detail-section" aria-labelledby="filings-heading">
        <h2 id="filings-heading">备案与登记</h2>
        {filings.length ? (
          <ul className="record-list">
            {filings.map((filing) => (
              <li key={`${filing.filing_type}:${filing.filing_number}`}>
                <div><strong>{filingLabels[filing.filing_type] ?? filing.filing_type}</strong><VerificationBadge status={filing.verification_status} /><span>{filing.filing_status ?? "状态待确认"}</span></div>
                <ExternalTextLink href={filing.detail_url}>{filing.filing_number}</ExternalTextLink>
                <p>{[filing.filing_name, filing.filing_authority, filing.filing_date].filter(Boolean).join(" · ")}</p>
              </li>
            ))}
          </ul>
        ) : <p className="section-empty">暂无公开备案资料</p>}
      </section>

      {profileFields.length ? (
        <section className="detail-section" aria-labelledby="profile-fields-heading">
          <h2 id="profile-fields-heading">公司画像补充</h2>
          <ul className="record-list">
            {profileFields.map((field) => (
              <li key={field.field_key}>
                <div><strong>{field.field_key}</strong><VerificationBadge status={field.verification_status} /></div>
                <span>{profileFieldValue(field.value)}</span>
                <p>采集于 {field.collected_at.slice(0, 10)}</p>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className="detail-section" aria-labelledby="evidence-heading">
        <h2 id="evidence-heading">资料依据</h2>
        {sources.length ? (
          <CollapsibleList items={sources} limit={5} className="record-list" itemKey={source => `${source.provider}:${source.url}`} renderItem={source => <SourceRowContent source={source} />} />
        ) : <p className="section-empty">暂无可展示的资料依据</p>}
      </section>
    </>
  );
}

function SourceRowContent({ source }: { source: CompanyDetail["sources"][number] }) {
  const pendingFields = pendingVerificationFields(source.field_verification);
  const coveredFields = source.covered_fields ?? [];
  const fetchedDate = source.fetched_at?.slice(0, 10) ?? "时间未知";
  return (
    <>
      <div><strong>{providerLabels[source.provider] ?? source.provider}</strong><span>置信度 {source.confidence}</span></div>
      <ExternalTextLink href={source.url}>{source.title ?? source.url}</ExternalTextLink>
      <p>覆盖字段：{coveredFields.join("、") || "未标注"} · 获取于 {fetchedDate}{pendingFields.length ? ` · 待核验：${pendingFields.join("、")}` : ""}</p>
    </>
  );
}

export function CompanyDetailPage() {
  const { companyId = "" } = useParams();
  const [company, setCompany] = useState<CompanyDetail | null>(null);
  const [detailState, setDetailState] = useState<"loading" | "ready" | "error" | "not-found">("loading");
  const [detailRetry, setDetailRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setDetailState("loading");
    api.getCompany(companyId, controller.signal).then((result) => {
      setCompany(result);
      setDetailState("ready");
    }).catch((error: unknown) => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setDetailState(error instanceof ApiError && error.status === 404 ? "not-found" : "error");
    });
    return () => controller.abort();
  }, [companyId, detailRetry]);

  return (
    <main>
      <header className="app-header">
        <div className="content-width header-content">
          <Link className="product-name" to="/list" aria-label="AI 公司榜首页"><span aria-hidden="true">企</span>AI 职业公司榜</Link>
          <nav className="primary-nav" aria-label="主导航"><Link to="/list">AI 榜单</Link><Link className="active" to="/companies">公司目录</Link></nav>
        </div>
      </header>
      <div className="content-width detail-workspace">
        <Link className="back-link" to="/companies"><ArrowLeft aria-hidden="true" size={16} />返回公司列表</Link>
        {detailState === "loading" ? <div className="detail-state" role="status" aria-label="正在加载公司详情">正在加载公司详情…</div> : null}
        {detailState === "not-found" ? <div className="detail-state" role="status" aria-live="polite" aria-atomic="true"><h1>未找到这家公司</h1><p>该公司可能尚未收录，或链接已经失效。</p></div> : null}
        {detailState === "error" ? (
          <div className="detail-state error-message" role="alert"><div><h1>公司详情加载失败</h1><p>请检查网络连接后重试。</p></div><button className="secondary-button" type="button" onClick={() => setDetailRetry((value) => value + 1)}><RotateCw aria-hidden="true" size={16} />重新加载</button></div>
        ) : null}
        {detailState === "ready" && company ? (
          <article>
            <DetailContent company={company} />
            <section className="detail-section jobs-section" aria-labelledby="jobs-heading">
              <div className="section-heading"><h2 id="jobs-heading">职位信息</h2></div>
              <p className="jobs-placeholder">职位功能即将开放。公司榜单与资料页当前不采集或展示职位数据。</p>
            </section>
          </article>
        ) : null}
      </div>
    </main>
  );
}
