import { ArrowLeft, ArrowRight, ExternalLink, RotateCw } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { safeHttpUrl } from "../api/http-url";
import type {
  CompanyListItem,
  CompanyScale,
  CompanySort,
  FundingStage,
  Page,
} from "../api/types";

type CompanyResultsProps = {
  data: Page<CompanyListItem> | null;
  error: boolean;
  loading: boolean;
  sort: CompanySort;
  hasActiveFilters: boolean;
  onSortChange: (sort: CompanySort) => void;
  onPageChange: (page: number) => void;
  onClear: () => void;
  onRetry: () => void;
  emptyQueryStatus: React.ReactNode;
};

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

const locationLabels: Record<string, string> = {
  Beijing: "北京",
  Shanghai: "上海",
  Hangzhou: "杭州",
  Shenzhen: "深圳",
};

function CompanyLogo({ company }: { company: CompanyListItem }) {
  const [failed, setFailed] = useState(false);
  const fallback = company.canonical_name.trim().slice(0, 2).toUpperCase();
  const logoUrl = safeHttpUrl(company.logo_url);

  return logoUrl && !failed ? (
    <img
      className="company-logo"
      src={logoUrl}
      alt=""
      width="44"
      height="44"
      loading="lazy"
      onError={() => setFailed(true)}
    />
  ) : (
    <span className="company-logo logo-fallback" aria-hidden="true">
      {fallback}
    </span>
  );
}

function CompanyRow({ company }: { company: CompanyListItem }) {
  const websiteUrl = safeHttpUrl(company.website);

  return (
    <li className="company-row">
      <CompanyLogo company={company} />
      <div className="company-copy">
        <div className="company-title-line">
          <h3><Link className="company-detail-link" to={`/companies/${company.id}`}>{company.canonical_name}</Link></h3>
          <span className={`ranking-status ranking-status--${company.ranking_status ?? "observation"}`}>{company.rank ? `第 ${company.rank} 名` : "观察中"}</span>
          <span>{company.city ? locationLabels[company.city] ?? company.city : "城市待确认"}</span>
          {company.campus_job_count ? <span className="opportunity-tag">校招 {company.campus_job_count}</span> : null}
          {company.internship_job_count ? <span className="opportunity-tag">实习 {company.internship_job_count}</span> : null}
        </div>
        <p className="company-tags">
          {[company.industry, company.sub_industry, fundingLabels[company.funding_stage], scaleLabels[company.scale]]
            .filter(Boolean)
            .join(" · ")}
        </p>
        <p className="company-ranking-summary">{company.ranking_score ?? 0} 分 · {company.company_stage === "early" ? "早期" : company.company_stage === "mature" ? "成熟" : "成长"}阶段</p>
        {company.description ? <p className="company-description">{company.description}</p> : null}
      </div>
      {websiteUrl ? (
        <a
          className="company-website"
          href={websiteUrl}
          target="_blank"
          rel="noreferrer"
          aria-label={`访问 ${company.canonical_name} 官网`}
        >
          官网
          <ExternalLink aria-hidden="true" size={15} />
        </a>
      ) : (
        <span className="website-unavailable">官网待确认</span>
      )}
    </li>
  );
}


function LoadingRows() {
  return (
    <div className="loading-results" role="status" aria-label="正在加载公司">
      <span className="sr-only">正在加载公司</span>
      {[0, 1, 2].map((row) => (
        <div className="skeleton-row" key={row} aria-hidden="true">
          <span className="skeleton-logo" />
          <span className="skeleton-lines"><i /><i /><i /></span>
        </div>
      ))}
    </div>
  );
}

export function CompanyResults({
  data,
  error,
  loading,
  sort,
  hasActiveFilters,
  onSortChange,
  onPageChange,
  onClear,
  onRetry,
  emptyQueryStatus,
}: CompanyResultsProps) {
  if (loading) return <LoadingRows />;

  if (error) {
    return (
      <div className="result-message error-message" role="alert">
        <div>
          <h2>公司列表加载失败</h2>
          <p>请检查网络连接后重试。</p>
        </div>
        <button className="secondary-button" type="button" onClick={onRetry}>
          <RotateCw aria-hidden="true" size={16} />
          重新加载
        </button>
      </div>
    );
  }

  const pageCount = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  if (!data || data.total === 0) {
    if (emptyQueryStatus) return emptyQueryStatus;
    return (
      <div className="result-message empty-message" role="status" aria-live="polite" aria-atomic="true">
        <div>
          <h2>没有找到符合条件的公司</h2>
          <p>尝试缩短关键词或减少筛选条件。</p>
        </div>
        <button className="secondary-button" type="button" onClick={onClear} disabled={!hasActiveFilters}>
          清除全部筛选
        </button>
      </div>
    );
  }

  if (data.items.length === 0 && data.page > pageCount) {
    return (
      <div className="result-message empty-message" role="status" aria-live="polite" aria-atomic="true">
        <div>
          <h2>当前页超出结果范围</h2>
          <p>结果共有 {pageCount} 页，请返回最后一个有效页面。</p>
        </div>
        <button className="secondary-button" type="button" onClick={() => onPageChange(pageCount)}>
          返回第 {pageCount} 页
        </button>
      </div>
    );
  }

  return (
    <section className="results" aria-labelledby="result-heading">
      <div className="results-toolbar">
        <div>
          <h2 id="result-heading">公司结果</h2>
          <p role="status" aria-live="polite" aria-atomic="true">共 {data.total} 家</p>
        </div>
        <label className="sort-control">
          <span>排序</span>
          <select value={sort} onChange={(event) => onSortChange(event.target.value as CompanySort)}>
            <option value="relevance">相关度</option>
            <option value="updated_at">最近更新</option>
            <option value="name">公司名称</option>
          </select>
        </label>
      </div>
      <ul className="company-list">
        {data.items.map((company) => <CompanyRow company={company} key={company.id} />)}
      </ul>
      <nav className="pagination" aria-label="公司结果分页">
        <button
          className="icon-button"
          type="button"
          onClick={() => onPageChange(data.page - 1)}
          disabled={data.page <= 1}
          aria-label="上一页"
          title="上一页"
        >
          <ArrowLeft aria-hidden="true" size={18} />
        </button>
        <span aria-live="polite">第 {data.page} / {pageCount} 页</span>
        <button
          className="icon-button"
          type="button"
          onClick={() => onPageChange(data.page + 1)}
          disabled={data.page >= pageCount}
          aria-label="下一页"
          title="下一页"
        >
          <ArrowRight aria-hidden="true" size={18} />
        </button>
      </nav>
    </section>
  );
}
