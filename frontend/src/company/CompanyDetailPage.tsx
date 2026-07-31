import { ArrowLeft, ExternalLink, RotateCw } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, ApiError } from "../api/client";
import { safeHttpUrl } from "../api/http-url";
import type { CompanyDetail, JobListItem, Page } from "../api/types";
import { JobList } from "./JobList";

const fundingLabels: Record<string, string> = {
  private: "未公开",
  angel: "天使轮",
  series_a: "A 轮",
  series_b: "B 轮",
  series_c: "C 轮及以后",
  ipo: "已上市",
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
  return (
    <>
      <div className="detail-identity">
        <CompanyLogo company={company} />
        <div>
          <h1>{company.canonical_name}</h1>
          <p className="detail-tags">
            {[company.industry, company.sub_industry, fundingLabels[company.funding_stage] ?? company.funding_stage, company.scale, company.city]
              .filter(Boolean).join(" · ")}
          </p>
        </div>
        {website ? <a className="secondary-button detail-website" href={website} target="_blank" rel="noreferrer">公司官网<ExternalLink aria-hidden="true" size={15} /></a> : null}
      </div>
      {company.description ? <p className="detail-description">{company.description}</p> : null}
      <dl className="company-facts">
        <div><dt>别名</dt><dd>{company.aliases.length ? company.aliases.join("、") : "暂无别名"}</dd></div>
        <div><dt>职位记录</dt><dd>{company.job_count} 个</dd></div>
        <div><dt>当前在招</dt><dd>{activeJobCount === null ? "加载中" : `${activeJobCount} 个`}</dd></div>
        <div><dt>资料更新</dt><dd>{company.updated_at.slice(0, 10)}</dd></div>
      </dl>

      <section className="detail-section" aria-labelledby="filings-heading">
        <h2 id="filings-heading">备案与登记</h2>
        {company.filings.length ? (
          <ul className="record-list">
            {company.filings.map((filing) => (
              <li key={`${filing.filing_type}:${filing.filing_number}`}>
                <div><strong>{filingLabels[filing.filing_type] ?? filing.filing_type}</strong><span>{filing.filing_status ?? "状态待确认"}</span></div>
                <ExternalTextLink href={filing.detail_url}>{filing.filing_number}</ExternalTextLink>
                <p>{[filing.filing_name, filing.filing_authority, filing.filing_date].filter(Boolean).join(" · ")}</p>
              </li>
            ))}
          </ul>
        ) : <p className="section-empty">暂无公开备案资料</p>}
      </section>

      <section className="detail-section" aria-labelledby="evidence-heading">
        <h2 id="evidence-heading">资料依据</h2>
        {company.sources.length ? (
          <ul className="record-list">
            {company.sources.map((source) => (
              <li key={`${source.provider}:${source.url}`}>
                <div><strong>{providerLabels[source.provider] ?? source.provider}</strong><span>置信度 {source.confidence}</span></div>
                <ExternalTextLink href={source.url}>{source.title ?? source.url}</ExternalTextLink>
                <p>覆盖字段：{source.covered_fields.join("、") || "未标注"} · 获取于 {source.fetched_at.slice(0, 10)}</p>
              </li>
            ))}
          </ul>
        ) : <p className="section-empty">暂无可展示的资料依据</p>}
      </section>
    </>
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
