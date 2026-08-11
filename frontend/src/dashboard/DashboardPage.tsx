import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { NameCount, OverviewStats } from "../api/types";

const STATUS_COLORS: Record<string, string> = {
  succeeded: "#5eead4",
  failed: "#fb7185",
  queued: "#fbbf24",
  running: "#60a5fa",
  partial: "#a78bfa",
};

const STATUS_LABELS: Record<string, string> = {
  succeeded: "成功",
  failed: "失败",
  queued: "队列中",
  running: "运行中",
  partial: "部分成功",
};

const TYPE_LABELS: Record<string, string> = {
  full_time: "全职",
  part_time: "兼职",
  internship: "实习",
  temporary: "临时",
  campus: "校招",
  experienced: "社招",
  unknown: "未知",
};

export function DashboardPage() {
  const [stats, setStats] = useState<OverviewStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    api.getStatsOverview(controller.signal)
      .then((data) => {
        setStats(data);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(true);
        setLoading(false);
      });
    return () => controller.abort();
  }, []);

  if (loading) {
    return (
      <main className="dashboard">
        <div className="dashboard-loading">
          <div className="scan-line" />
          <p>正在汇聚数据流…</p>
        </div>
      </main>
    );
  }

  if (error || !stats) {
    return (
      <main className="dashboard">
        <div className="dashboard-error">
          <p>数据通道中断</p>
          <Link to="/companies" className="dashboard-back">返回检索</Link>
        </div>
      </main>
    );
  }

  const companyDescPct = stats.companies_total > 0
    ? Math.round((stats.companies_with_description / stats.companies_total) * 100)
    : 0;
  const companyWebPct = stats.companies_total > 0
    ? Math.round((stats.companies_with_website / stats.companies_total) * 100)
    : 0;

  return (
    <main className="dashboard">
      <header className="dashboard-header">
        <div className="dashboard-header-content">
          <div className="dashboard-title-block">
            <span className="dashboard-pulse" />
            <h1>数据观测台</h1>
            <p>AI 公司检索 · 采集管线实时全景</p>
          </div>
          <nav className="dashboard-nav">
            <Link to="/companies" className="dashboard-nav-link">公司检索 →</Link>
          </nav>
        </div>
      </header>

      <section className="dashboard-grid">
        {/* KPI 卡片行 */}
        <article className="kpi-card kpi-companies">
          <div className="kpi-label">公司总数</div>
          <div className="kpi-value">{stats.companies_total.toLocaleString()}</div>
          <div className="kpi-meta">
            <span className="kpi-dot kpi-dot-green" />
            有描述 {stats.companies_with_description} · 有网站 {stats.companies_with_website}
          </div>
        </article>

        <article className="kpi-card kpi-jobs">
          <div className="kpi-label">职位总数</div>
          <div className="kpi-value">{stats.jobs_total}</div>
          <div className="kpi-meta">
            <span className="kpi-dot kpi-dot-cyan" />
            有城市标注 {stats.jobs_with_city}
          </div>
        </article>

        <article className="kpi-card kpi-runs">
          <div className="kpi-label">采集任务</div>
          <div className="kpi-value">{stats.crawl_runs_total}</div>
          <div className="kpi-meta">
            <span className="kpi-dot kpi-dot-amber" />
            采集覆盖率 {coveragePercent(stats)}%
          </div>
        </article>

        {/* 采集状态环形图 */}
        <article className="chart-card chart-donut">
          <h2 className="chart-title">采集管线状态</h2>
          <DonutChart data={stats.crawl_runs_by_status} />
          <div className="legend">
            {stats.crawl_runs_by_status.map((item) => (
              <div className="legend-item" key={item.name}>
                <span
                  className="legend-swatch"
                  style={{ background: STATUS_COLORS[item.name] ?? "#94a3b8" }}
                />
                <span className="legend-label">{STATUS_LABELS[item.name] ?? item.name}</span>
                <span className="legend-value">{item.count}</span>
              </div>
            ))}
          </div>
        </article>

        {/* 职位城市分布 */}
        <article className="chart-card chart-bars">
          <h2 className="chart-title">职位城市分布 · Top {stats.jobs_by_city.length}</h2>
          <BarChart data={stats.jobs_by_city} color="#5eead4" />
        </article>

        {/* 职位类型分布 */}
        <article className="chart-card chart-bars">
          <h2 className="chart-title">职位类型分布</h2>
          <BarChart
            data={stats.jobs_by_type.map((item) => ({
              ...item,
              name: TYPE_LABELS[item.name] ?? item.name,
            }))}
            color="#60a5fa"
          />
        </article>

        {/* 公司数据完整度 */}
        <article className="chart-card chart-completeness">
          <h2 className="chart-title">公司数据完整度</h2>
          <div className="completeness-list">
            <CompletenessBar
              label="公司描述"
              filled={stats.companies_with_description}
              total={stats.companies_total}
              percent={companyDescPct}
              color="#5eead4"
            />
            <CompletenessBar
              label="官方网站"
              filled={stats.companies_with_website}
              total={stats.companies_total}
              percent={companyWebPct}
              color="#60a5fa"
            />
            <CompletenessBar
              label="城市标注"
              filled={stats.jobs_with_city}
              total={stats.jobs_total}
              percent={stats.jobs_total > 0 ? Math.round((stats.jobs_with_city / stats.jobs_total) * 100) : 0}
              color="#a78bfa"
            />
          </div>
        </article>

        {/* 融资阶段分布 */}
        <article className="chart-card chart-bars">
          <h2 className="chart-title">公司融资阶段</h2>
          <BarChart data={stats.companies_by_funding_stage} color="#fbbf24" />
        </article>
      </section>

      <footer className="dashboard-footer">
        <span>数据观测台 · 实时同步自采集管线</span>
      </footer>
    </main>
  );
}

function coveragePercent(stats: OverviewStats): number {
  if (stats.crawl_runs_total === 0) return 0;
  const done = stats.crawl_runs_by_status
    .filter((s) => s.name === "succeeded" || s.name === "partial" || s.name === "failed")
    .reduce((sum, s) => sum + s.count, 0);
  return Math.round((done / stats.crawl_runs_total) * 100);
}

function DonutChart({ data }: { data: NameCount[] }) {
  const total = data.reduce((sum, item) => sum + item.count, 0);
  if (total === 0) {
    return <div className="chart-empty">暂无数据</div>;
  }
  const radius = 70;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  const segments = data.map((item) => {
    const fraction = item.count / total;
    const length = fraction * circumference;
    const segment = {
      color: STATUS_COLORS[item.name] ?? "#94a3b8",
      dashArray: `${length} ${circumference - length}`,
      dashOffset: -offset,
      label: item.name,
    };
    offset += length;
    return segment;
  });

  return (
    <div className="donut-wrapper">
      <svg viewBox="0 0 180 180" className="donut-svg">
        <circle cx="90" cy="90" r={radius} fill="none" stroke="#1e293b" strokeWidth="18" />
        {segments.map((seg, i) => (
          <circle
            key={i}
            cx="90"
            cy="90"
            r={radius}
            fill="none"
            stroke={seg.color}
            strokeWidth="18"
            strokeDasharray={seg.dashArray}
            strokeDashoffset={seg.dashOffset}
            transform="rotate(-90 90 90)"
          />
        ))}
        <text x="90" y="85" textAnchor="middle" className="donut-center-value">
          {total}
        </text>
        <text x="90" y="105" textAnchor="middle" className="donut-center-label">
          总任务
        </text>
      </svg>
    </div>
  );
}

function BarChart({ data, color }: { data: NameCount[]; color: string }) {
  if (data.length === 0) {
    return <div className="chart-empty">暂无数据</div>;
  }
  const max = Math.max(...data.map((d) => d.count));
  return (
    <div className="bar-chart">
      {data.map((item) => {
        const widthPct = max > 0 ? (item.count / max) * 100 : 0;
        return (
          <div className="bar-row" key={item.name}>
            <span className="bar-label" title={item.name}>
              {item.name}
            </span>
            <div className="bar-track">
              <div
                className="bar-fill"
                style={{ width: `${widthPct}%`, background: color }}
              />
            </div>
            <span className="bar-value">{item.count}</span>
          </div>
        );
      })}
    </div>
  );
}

function CompletenessBar({
  label,
  filled,
  total,
  percent,
  color,
}: {
  label: string;
  filled: number;
  total: number;
  percent: number;
  color: string;
}) {
  return (
    <div className="completeness-item">
      <div className="completeness-header">
        <span className="completeness-label">{label}</span>
        <span className="completeness-percent" style={{ color }}>
          {percent}%
        </span>
      </div>
      <div className="completeness-track">
        <div
          className="completeness-fill"
          style={{ width: `${percent}%`, background: color }}
        />
      </div>
      <div className="completeness-meta">
        {filled} / {total}
      </div>
    </div>
  );
}
