import { ArrowLeft, ArrowRight, ExternalLink, RotateCw } from "lucide-react";

import { safeHttpUrl } from "../api/http-url";
import type { JobListItem, Page } from "../api/types";

const jobTypeLabels: Record<JobListItem["job_type"], string> = {
  full_time: "全职",
  internship: "实习",
  campus: "校招",
  experienced: "社招",
  unknown: "类型待确认",
};

const providerLabels: Record<string, string> = {
  company_site: "公司官网投递",
  zhihu: "知乎投递",
  boss: "BOSS 直聘投递",
  liepin: "猎聘投递",
  lagou: "拉勾投递",
};

function salaryLabel(job: JobListItem) {
  const { salary_min_monthly: minimum, salary_max_monthly: maximum, salary_months: months } = job;
  if (minimum === null && maximum === null) return "薪资面议";
  const monthly = minimum !== null && maximum !== null
    ? `${minimum.toLocaleString("zh-CN")}-${maximum.toLocaleString("zh-CN")} 元/月`
    : minimum !== null
      ? `${minimum.toLocaleString("zh-CN")} 元/月起`
      : `最高 ${maximum?.toLocaleString("zh-CN")} 元/月`;
  return `${monthly}${months ? ` · ${months} 薪` : ""}`;
}

function JobRow({ job }: { job: JobListItem }) {
  return (
    <li className="job-row">
      <div className="job-heading-line">
        <h3>{job.title}</h3>
        <span className={job.is_active ? "status-active" : "status-closed"}>
          {job.is_active ? "招聘中" : "已结束"}
        </span>
      </div>
      <p className="job-facts">
        {jobTypeLabels[job.job_type]} · {job.city} · {salaryLabel(job)}
        {job.posted_at ? ` · 发布于 ${job.posted_at}` : ""}
      </p>
      <p className="job-description">{job.description}</p>
      <div className="application-links" aria-label={`${job.title} 投递渠道`}>
        {job.sources.map((source) => {
          const href = safeHttpUrl(source.apply_url);
          if (!href) return null;
          return (
            <a key={`${source.provider}:${source.apply_url}`} href={href} target="_blank" rel="noreferrer">
              {providerLabels[source.provider] ?? `${source.provider}投递`}
              <ExternalLink aria-hidden="true" size={14} />
            </a>
          );
        })}
      </div>
    </li>
  );
}

export function JobList({
  data,
  loading,
  error,
  onPageChange,
  onRetry,
}: {
  data: Page<JobListItem> | null;
  loading: boolean;
  error: boolean;
  onPageChange: (page: number) => void;
  onRetry: () => void;
}) {
  if (loading) {
    return <div className="jobs-state" role="status" aria-label="正在加载职位">正在加载职位…</div>;
  }
  if (error) {
    return (
      <div className="jobs-state error-message" role="alert">
        <span>职位加载失败</span>
        <button className="secondary-button" type="button" onClick={onRetry}>
          <RotateCw aria-hidden="true" size={16} />重新加载
        </button>
      </div>
    );
  }
  if (!data || data.total === 0) {
    return <div className="jobs-state" role="status" aria-label="暂无在招职位" aria-live="polite">暂无在招职位</div>;
  }

  const pageCount = Math.max(1, Math.ceil(data.total / data.page_size));
  return (
    <>
      <ul className="job-list">{data.items.map((job) => <JobRow key={job.id} job={job} />)}</ul>
      <nav className="pagination" aria-label="职位分页">
        <button className="icon-button" type="button" disabled={data.page <= 1} onClick={() => onPageChange(data.page - 1)} aria-label="上一页职位" title="上一页职位">
          <ArrowLeft aria-hidden="true" size={18} />
        </button>
        <span aria-live="polite">第 {data.page} / {pageCount} 页</span>
        <button className="icon-button" type="button" disabled={data.page >= pageCount} onClick={() => onPageChange(data.page + 1)} aria-label="下一页职位" title="下一页职位">
          <ArrowRight aria-hidden="true" size={18} />
        </button>
      </nav>
    </>
  );
}
