import { ArrowLeft, ExternalLink, RotateCw } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, ApiError } from "../api/client";
import { safeHttpUrl } from "../api/http-url";
import type {
  CompanyDetail,
  CompanyScale,
  FundingStage,
  JobListItem,
  Page,
  VerificationStatus,
} from "../api/types";
import { JobList } from "./JobList";

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

function businessRegistrationNumber(filings: CompanyDetail["filings"]) {
  return filings.find((filing) => filing.filing_type === "business_license")?.filing_number ?? null;
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

function DetailContent({ company, activeJobCount }: { company: CompanyDetail; activeJobCount: number | null }) {
  const website = safeHttpUrl(company.website);
  const aliases = company.aliases ?? [];
  const filings = company.filings ?? [];
  const profileFields = company.profile_fields ?? [];
  const sources = company.sources ?? [];
  const registrationNumber = businessRegistrationNumber(filings);
  const coverage = company.recruiting_coverage;
  return (
    <>
      <div className="detail-identity">
        <CompanyLogo company={company} />
        <div>
          <h1>{company.canonical_name}</h1>
          <p className="detail-tags">
            {[company.industry, company.sub_industry, fundingLabels[company.funding_stage], scaleLabels[company.scale], company.city]
              .filter(Boolean).join(" · ")}
          </p>
        </div>
        {website ? <a className="secondary-button detail-website" href={website} target="_blank" rel="noreferrer">公司官网<ExternalLink aria-hidden="true" size={15} /></a> : null}
      </div>
      {company.description ? <p className="detail-description">{company.description}</p> : null}
      <section className="recruiting-coverage" aria-label="Recruiting coverage">
        <strong>{coverage.status === "active_roles" ? "正在招聘" : coverage.status === "empty_confirmed" ? "已核验暂无职位" : coverage.status === "entry_discovery_pending" ? "招聘入口待发现" : coverage.status === "collection_incomplete" ? "招聘信息待复查" : "招聘信息已过期"}</strong>
        {coverage.status === "active_roles" && coverage.active_job_count !== null ? <span>{` · ${coverage.active_job_count} 个在招职位`}</span> : null}
        <span>{coverage.last_checked_at ? ` · 最近核验 ${coverage.last_checked_at.slice(0, 10)}` : " · 尚未核验"}</span>
      </section>
      <dl className="company-facts">
        <div><dt>别名</dt><dd>{aliases.length ? aliases.join("、") : "暂无别名"}</dd></div>
        <div><dt>职位记录</dt><dd>{company.job_count ?? 0} 个</dd></div>
        <div><dt>当前在招</dt><dd>{activeJobCount === null ? "加载中" : `${activeJobCount} 个`}</dd></div>
        <div><dt>资料更新</dt><dd>{company.updated_at.slice(0, 10)}</dd></div>
      </dl>

      <section className="detail-section" aria-labelledby="profile-heading">
        <h2 id="profile-heading">基本信息</h2>
        <dl className="company-profile">
          <div><dt>所属行业</dt><dd>{[company.industry, company.sub_industry].filter(Boolean).join(" · ") || "待补充"}</dd></div>
          <div><dt>融资阶段</dt><dd>{fundingLabels[company.funding_stage]}</dd></div>
          <div><dt>团队规模</dt><dd>{scaleLabels[company.scale]}</dd></div>
          <div><dt>主要城市</dt><dd>{company.city ?? "待补充"}</dd></div>
          <div><dt>公司总部</dt><dd>{company.headquarters ?? "待补充"}</dd></div>
          <div><dt>成立年份</dt><dd>{company.founded_year ? `${company.founded_year} 年` : "待补充"}</dd></div>
          <div><dt>统一社会信用代码</dt><dd>{registrationNumber ?? "待补充"}</dd></div>
          <div><dt>公司官网</dt><dd>{website ? <a href={website} target="_blank" rel="noreferrer">访问官网<ExternalLink aria-hidden="true" size={14} /></a> : "待补充"}</dd></div>
        </dl>
      </section>

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
          <ul className="record-list">
            {sources.map((source) => (
              <SourceRow key={`${source.provider}:${source.url}`} source={source} />
            ))}
          </ul>
        ) : <p className="section-empty">暂无可展示的资料依据</p>}
      </section>
    </>
  );
}

function SourceRow({ source }: { source: CompanyDetail["sources"][number] }) {
  const pendingFields = pendingVerificationFields(source.field_verification);
  const coveredFields = source.covered_fields ?? [];
  const fetchedDate = source.fetched_at?.slice(0, 10) ?? "时间未知";
  return (
    <li>
      <div><strong>{providerLabels[source.provider] ?? source.provider}</strong><span>置信度 {source.confidence}</span></div>
      <ExternalTextLink href={source.url}>{source.title ?? source.url}</ExternalTextLink>
      <p>覆盖字段：{coveredFields.join("、") || "未标注"} · 获取于 {fetchedDate}{pendingFields.length ? ` · 待核验：${pendingFields.join("、")}` : ""}</p>
    </li>
  );
}

export function CompanyDetailPage() {
  const { companyId = "" } = useParams();
  const [company, setCompany] = useState<CompanyDetail | null>(null);
  const [detailState, setDetailState] = useState<"loading" | "ready" | "error" | "not-found">("loading");
  const [detailRetry, setDetailRetry] = useState(0);
  const [jobs, setJobs] = useState<Page<JobListItem> | null>(null);
  const [jobsLoading, setJobsLoading] = useState(true);
  const [jobsError, setJobsError] = useState(false);
  const [jobsPage, setJobsPage] = useState(1);
  const [jobsRetry, setJobsRetry] = useState(0);

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

  useEffect(() => {
    const controller = new AbortController();
    setJobsLoading(true);
    setJobsError(false);
    api.getCompanyJobs(companyId, jobsPage, controller.signal).then((result) => {
      setJobs(result);
      setJobsLoading(false);
    }).catch((error: unknown) => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setJobsError(true);
      setJobsLoading(false);
    });
    return () => controller.abort();
  }, [companyId, jobsPage, jobsRetry]);

  return (
    <main>
      <header className="app-header">
        <div className="content-width header-content">
          <Link className="product-name" to="/companies" aria-label="AI 公司检索首页"><span aria-hidden="true">企</span>AI 公司检索</Link>
          <p>面向求职决策的公司资料库</p>
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
            <DetailContent company={company} activeJobCount={jobs?.total ?? null} />
            <section className="detail-section jobs-section" aria-labelledby="jobs-heading">
              <div className="section-heading"><h2 id="jobs-heading">在招职位</h2><span>{jobs ? `${jobs.total} 个在招职位` : "正在加载职位"}</span></div>
              <JobList data={jobs} loading={jobsLoading} error={jobsError} onPageChange={setJobsPage} onRetry={() => setJobsRetry((value) => value + 1)} />
            </section>
          </article>
        ) : null}
      </div>
    </main>
  );
}
