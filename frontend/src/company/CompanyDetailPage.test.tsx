import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import type { CompanyDetail } from "../api/types";
import { CompanyDetailPage } from "./CompanyDetailPage";

const companyId = "11111111-1111-4111-8111-111111111111";
const company: CompanyDetail = {
  id: companyId, canonical_name: "DeepSeek", industry: "人工智能", sub_industry: "基础模型",
  funding_stage: "unknown", scale: "200_to_499", city: "杭州", headquarters: "杭州",
  founded_year: 2023, logo_url: null, website: "https://www.deepseek.com/", description: "专注于通用人工智能基础模型研发。",
  established_at: "2023-07-17", province: "浙江省", district: "拱墅区", company_type: "有限责任公司",
  registered_capital: "1000万人民币", paid_in_capital: "500万人民币", industry_sector: "信息传输、软件和信息技术服务业", industry_middle: "软件开发",
  insured_employee_count: 320, employee_report_year: 2025, business_scope: "人工智能基础软件开发。".repeat(12), latest_funding_round: "A+轮",
  last_collected_at: "2026-07-30T08:00:00Z", created_at: "2026-07-01T00:00:00Z", updated_at: "2026-07-30T08:00:00Z",
  recruiting_coverage: { status: "entry_discovery_pending", active_job_count: null, last_checked_at: null, last_successful_at: null, freshness: "unknown", reason_code: null },
  ranking_status: "ranked", rank: 1, ranking_score: 86, company_stage: "growth",
  aliases: ["深度求索"], job_count: 0, filings: [], sources: [], profile_fields: [], funding_events: [{ round_label: "A+轮", announced_at: "2026-05-01", amount: null, currency: null, investors: ["示例资本"], verification_status: "verified" }],
  ranking_rule_version: "ai-long-term-v2", ranking_calculated_at: "2026-08-12T00:00:00Z",
  ranking_components: { ai_core: 27, market_validation: 20, growth_momentum: 19, industry_influence: 14, reliability: 6 },
  ranking_reason: "AI 核心性在同阶段公司中表现突出。", ranking_missing_fields: [],
  ranking_signals: [{ category: "intellectual_property", signal_key: "ai_invention_patent", value: { title: "大模型推理方法" }, event_date: "2026-06-01" }],
};

function response(body: unknown, status = 200) { return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })); }

function renderDetail(fetchImpl: (input: RequestInfo | URL) => Promise<Response> = (input) => String(input).includes("/jobs") ? response({ items: [], page: 1, page_size: 20, total: 0 }) : response(company), from?: string) {
  vi.stubGlobal("fetch", vi.fn(fetchImpl));
  window.history.replaceState(from ? { usr: { from }, key: "test", idx: 0 } : {}, "", `/companies/${companyId}`);
  render(<BrowserRouter><Routes><Route path="/companies/:companyId" element={<CompanyDetailPage />} /></Routes></BrowserRouter>);
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("CompanyDetailPage", () => {
  it("shows ranking, public company data, evidence and the job placeholder", async () => {
    renderDetail();
    expect(await screen.findByRole("heading", { name: "DeepSeek", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("榜单第 1 名")).toBeInTheDocument();
    expect(screen.getByText("86")).toBeInTheDocument();
    expect(screen.getByText(/AI 发明专利：大模型推理方法/)).toBeInTheDocument();
    expect(await screen.findByText(/暂未发现有效职位/)).toBeInTheDocument();
    expect(screen.getAllByText("A+轮").length).toBeGreaterThan(0);
    expect(screen.getByText("1000万人民币")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /展开全文/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("统一社会信用代码")).not.toBeInTheDocument();
  });

  it("requests the early-career jobs endpoint once", async () => {
    const requests: string[] = [];
    renderDetail((input) => { requests.push(String(input)); return String(input).includes("/jobs") ? response({ items: [], page: 1, page_size: 20, total: 0 }) : response(company); });
    await screen.findByRole("heading", { name: "DeepSeek", level: 1 });
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(requests.filter(url => url.includes("/jobs"))).toHaveLength(1);
  });

  it("returns to the originating ranking stage and rejects unsafe return paths", async () => {
    renderDetail(undefined, "/list?stage=growth");
    expect(await screen.findByRole("link", { name: "返回 AI 榜单" })).toHaveAttribute("href", "/list?stage=growth");
    cleanup();
    renderDetail(undefined, "https://example.com/phishing");
    expect(await screen.findByRole("link", { name: "返回公司列表" })).toHaveAttribute("href", "/companies");
  });

  it("shows explicit loading and not-found states", async () => {
    const pending = new Promise<Response>(() => undefined);
    renderDetail(() => pending);
    expect(screen.getByRole("status", { name: "正在加载公司详情" })).toBeInTheDocument();
    cleanup();
    renderDetail(() => response({ error: { code: "company_not_found" } }, 404));
    expect(await screen.findByRole("heading", { name: "未找到这家公司" })).toBeInTheDocument();
  });
});
