import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RankingList } from "../api/types";
import { RankingListPage } from "./RankingListPage";

const ranked: RankingList = {
  industry: "ai", rule_version: "ai-long-term-v2", calculated_at: "2026-08-12T00:00:00Z",
  ranked_total: 98, observation_total: 2, page: 1, page_size: 100, total: 1,
  items: [{ company_id: "company-1", company_name: "示例智能", rank: 1, status: "ranked", total_score: 86, company_stage: "growth", component_scores: { ai_core: 27, market_validation: 20, growth_momentum: 19, industry_influence: 14, reliability: 6 }, reason: "AI 核心性突出", missing_fields: [] }],
};
const observation: RankingList = { ...ranked, total: 1, items: [{ ...ranked.items[0], company_id: "company-2", company_name: "待观察公司", rank: null, status: "observation", total_score: 0, reason: "AI 相关性证据不足" }] };

afterEach(() => vi.unstubAllGlobals());

function setup() {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => Promise.resolve(new Response(JSON.stringify(String(input).includes("observation") ? observation : ranked), { status: 200, headers: { "Content-Type": "application/json" } }))));
  render(<MemoryRouter><RankingListPage /></MemoryRouter>);
}

describe("RankingListPage", () => {
  it("shows the fixed public ranking and separate observation pool", async () => {
    setup();
    expect(await screen.findByText("示例智能")).toBeInTheDocument();
    expect(screen.getByText("待观察公司")).toBeInTheDocument();
    expect(screen.getByText("98")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看示例智能详情" })).toHaveAttribute("href", "/companies/company-1");
  });

  it("filters by stage through the same ranking endpoint", async () => {
    setup();
    await screen.findByText("示例智能");
    await userEvent.click(screen.getByRole("button", { name: "成熟" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining("stage=mature"), expect.anything()));
  });
});
