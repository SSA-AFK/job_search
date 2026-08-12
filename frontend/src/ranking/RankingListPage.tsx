import { ArrowRight, Building2, ListFilter, RotateCw } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { RankingList, RankingMember } from "../api/types";

const stageLabels = { early: "早期", growth: "成长", mature: "成熟" } as const;
const components = [
  ["ai_core", "AI 核心性", 30],
  ["market_validation", "市场验证", 25],
  ["growth_momentum", "成长动能", 20],
  ["industry_influence", "行业影响", 15],
  ["reliability", "可靠性", 10],
] as const;

function Header() {
  return <header className="app-header"><div className="content-width header-content">
    <Link className="product-name" to="/list"><span aria-hidden="true">企</span>AI 职业公司榜</Link>
    <nav className="primary-nav" aria-label="主导航"><Link className="active" to="/list">AI 榜单</Link><Link to="/companies">公司目录</Link></nav>
  </div></header>;
}

function ScoreBars({ member }: { member: RankingMember }) {
  return <div className="score-bars" aria-label="五维评分">{components.map(([key, label, maximum]) => {
    const value = member.component_scores[key];
    return <div className="score-bar" key={key} title={`${label} ${value}/${maximum}`}>
      <span>{label}</span><i><b style={{ width: `${value / maximum * 100}%` }} /></i><strong>{value}</strong>
    </div>;
  })}</div>;
}

function RankingRow({ member }: { member: RankingMember }) {
  return <li className="ranking-row">
    <div className={`rank-mark ${member.rank && member.rank <= 3 ? "rank-mark--top" : ""}`}>{member.rank ?? "—"}</div>
    <div className="ranking-company"><div><Link to={`/companies/${member.company_id}`}>{member.company_name}</Link><span>{stageLabels[member.company_stage]}</span>{member.campus_job_count ? <span className="opportunity-tag">校招 {member.campus_job_count}</span> : null}{member.internship_job_count ? <span className="opportunity-tag">实习 {member.internship_job_count}</span> : null}</div><p>{member.reason}</p></div>
    <ScoreBars member={member} />
    <div className="ranking-total"><strong>{member.total_score}</strong><span>总分</span></div>
    <Link className="ranking-open" to={`/companies/${member.company_id}`} aria-label={`查看${member.company_name}详情`}><ArrowRight size={18} /></Link>
  </li>;
}

export function RankingListPage() {
  const [stage, setStage] = useState<"early" | "growth" | "mature" | undefined>();
  const [ranked, setRanked] = useState<RankingList | null>(null);
  const [observation, setObservation] = useState<RankingList | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController(); setError(false); setRanked(null);
    Promise.all([api.getAiRanking("ranked", stage, controller.signal), api.getAiRanking("observation", stage, controller.signal)])
      .then(([nextRanked, nextObservation]) => { setRanked(nextRanked); setObservation(nextObservation); })
      .catch((reason: unknown) => { if (!(reason instanceof DOMException && reason.name === "AbortError")) setError(true); });
    return () => controller.abort();
  }, [stage, retry]);

  return <main><Header /><div className="content-width ranking-workspace">
    <section className="ranking-intro"><div><h1>AI 职业公司榜</h1><p>关注公司的长期成长与行业地位。公司按相同规则与发展阶段校准，校招和实习机会仅作求职参考，不计入评分。</p></div>
      <div className="ranking-summary"><strong>{ranked?.ranked_total ?? 98}</strong><span>家正式入榜</span><strong>{ranked?.observation_total ?? 2}</strong><span>家观察</span></div>
    </section>
    <div className="ranking-toolbar"><div><ListFilter size={17} /><span>发展阶段</span>{([undefined, "early", "growth", "mature"] as const).map(value => <button className={stage === value ? "active" : ""} key={value ?? "all"} type="button" onClick={() => setStage(value)}>{value ? stageLabels[value] : "全部"}</button>)}</div>
      {ranked ? <p>规则 {ranked.rule_version} · 更新于 {ranked.calculated_at.slice(0, 10)}</p> : null}</div>
    {error ? <div className="result-message error-message" role="alert"><div><h2>榜单暂时无法加载</h2><p>请稍后重试。</p></div><button className="secondary-button" onClick={() => setRetry(value => value + 1)}><RotateCw size={16} />重新加载</button></div> : null}
    {!error && !ranked ? <div className="ranking-skeleton" aria-label="正在加载榜单">{Array.from({ length: 8 }, (_, index) => <i key={index} />)}</div> : null}
    {ranked ? <section aria-labelledby="ranked-heading"><div className="ranking-section-heading"><h2 id="ranked-heading">正式榜单</h2><span>{ranked.total} 家</span></div><ol className="ranking-list">{ranked.items.map(member => <RankingRow key={member.company_id} member={member} />)}</ol></section> : null}
    {observation && observation.items.length ? <section className="observation-section" aria-labelledby="observation-heading"><div className="ranking-section-heading"><div><h2 id="observation-heading">观察池</h2><p>AI 相关性证据尚不足，不分配正式名次。</p></div><span>{observation.total} 家</span></div><ul className="ranking-list">{observation.items.map(member => <RankingRow key={member.company_id} member={member} />)}</ul></section> : null}
    <footer className="ranking-method"><Building2 size={20} /><div><strong>评分如何产生？</strong><p>AI 核心性 30、市场验证 25、成长动能 20、行业影响力 15、可靠性 10；在早期、成长、成熟阶段内分别校准。无证据的维度得 0 分。</p></div></footer>
  </div></main>;
}
