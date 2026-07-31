import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import type { CompanyDetail, JobListItem, Page } from "../api/types";
import { CompanyDetailPage } from "./CompanyDetailPage";

const companyId = "11111111-1111-4111-8111-111111111111";

const company: CompanyDetail = {
  id: companyId,
  canonical_name: "DeepSeek",
  industry: "Artificial Intelligence",
  sub_industry: "Foundation Models",
  funding_stage: "private",
  scale: "100-499",
  city: "Hangzhou",
  logo_url: null,
  website: "https://www.deepseek.com/",
  description: "专注于通用人工智能基础模型研发。",
  last_collected_at: "2026-07-30T08:00:00Z",
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-30T08:00:00Z",
  aliases: ["深度求索"],
  job_count: 2,
  filings: [
    {
      filing_type: "icp",
      filing_number: "浙ICP备2023025841号",
      filing_name: "DeepSeek official website",
      filing_authority: "Ministry of Industry and Information Technology",
      filing_date: "2023-08-01",
      filing_status: "active",
      detail_url: "https://beian.miit.gov.cn/",
    },
  ],
  sources: [
    {
      provider: "official_registry",
      url: "https://registry.example.com/deepseek",
      title: "DeepSeek registry record",
      covered_fields: ["canonical_name", "website"],
      confidence: "0.975",
      published_at: "2026-06-01T00:00:00Z",
      fetched_at: "2026-07-31T00:00:00Z",
    },
  ],
};

const job: JobListItem = {
  id: "22222222-2222-4222-8222-222222222222",
  company_id: companyId,
  title: "Large Model Algorithm Engineer",
  job_type: "full_time",
  city: "Hangzhou",
  salary_min_monthly: 30000,
  salary_max_monthly: 60000,
  salary_months: 16,
  description: "负责大模型训练与推理优化。",
  posted_at: "2026-07-20",
  is_active: true,
  sources: [
    { provider: "company_site", apply_url: "https://example.com/jobs/1" },
    { provider: "zhihu", apply_url: "https://jobs.example.com/1" },
    { provider: "unsafe", apply_url: "javascript:alert(document.domain)" },
  ],
  created_at: "2026-07-20T00:00:00Z",
  updated_at: "2026-07-21T00:00:00Z",
};

function response(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  }));
}

function jobsPage(items: JobListItem[], page = 1, total = items.length): Page<JobListItem> {
  return { items, page, page_size: 10, total };
}

function renderCompanyDetail(fetchImpl?: (input: RequestInfo | URL) => Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn(fetchImpl ?? ((input: RequestInfo | URL) => {
    const url = new URL(String(input), window.location.origin);
    if (url.pathname === `/api/v1/companies/${companyId}/jobs`) {
      return response(jobsPage([job], 1, 11));
    }
    if (url.pathname === `/api/v1/companies/${companyId}`) return response(company);
    return response({ error: { code: "not_found", message: "Resource not found" } }, 404);
  })));
  window.history.replaceState({}, "", `/companies/${companyId}`);
  return render(
    <BrowserRouter>
      <Routes>
        <Route path="/companies/:companyId" element={<CompanyDetailPage />} />
      </Routes>
    </BrowserRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("CompanyDetailPage", () => {
  it("keeps source labels paired with their application links", async () => {
    renderCompanyDetail();

    const companyLink = await screen.findByRole("link", { name: "公司官网投递" });
    expect(companyLink).toHaveAttribute("href", "https://example.com/jobs/1");
    expect(companyLink).toHaveAttribute("target", "_blank");
    expect(companyLink).toHaveAttribute("rel", "noreferrer");
    expect(screen.getByRole("link", { name: "知乎投递" })).toHaveAttribute(
      "href",
      "https://jobs.example.com/1",
    );
    expect(screen.queryByRole("link", { name: "unsafe投递" })).not.toBeInTheDocument();
  });

  it("renders company identity, aliases, filings, evidence and job facts", async () => {
    renderCompanyDetail();

    expect(await screen.findByRole("heading", { name: "DeepSeek", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("深度求索")).toBeInTheDocument();
    expect(screen.getByText("浙ICP备2023025841号")).toBeInTheDocument();
    expect(screen.getByText("DeepSeek registry record")).toBeInTheDocument();
    expect(screen.getByText("Large Model Algorithm Engineer")).toBeInTheDocument();
    expect(screen.getByText(/30,000-60,000 元\/月 · 16 薪/)).toBeInTheDocument();
  });

  it("falls back instead of rendering a non-HTTP company logo", async () => {
    renderCompanyDetail((input) => String(input).includes("/jobs")
      ? response(jobsPage([]))
      : response({ ...company, logo_url: "data:image/svg+xml,<svg></svg>" }));

    await screen.findByRole("heading", { name: "DeepSeek", level: 1 });
    expect(document.querySelector("img.detail-logo")).not.toBeInTheDocument();
    expect(document.querySelector(".detail-logo.logo-fallback")).toHaveTextContent("DE");
  });

  it("shows explicit loading and not-found states", async () => {
    const pending = new Promise<Response>(() => undefined);
    const view = renderCompanyDetail(() => pending);
    expect(screen.getByRole("status", { name: "正在加载公司详情" })).toBeInTheDocument();

    view.unmount();
    renderCompanyDetail(() => response({
      error: { code: "company_not_found", message: "Company not found" },
    }, 404));
    const heading = await screen.findByRole("heading", { name: "未找到这家公司" });
    expect(heading.closest('[role="status"]')).toHaveAttribute("aria-live", "polite");
    expect(heading.closest('[role="status"]')).toHaveAttribute("aria-atomic", "true");
  });

  it("distinguishes all job records from the active job count", async () => {
    renderCompanyDetail((input) => String(input).includes("/jobs")
      ? response(jobsPage([job], 1, 1))
      : response(company));

    await screen.findByText("Large Model Algorithm Engineer");
    expect(screen.getByText("职位记录").nextElementSibling).toHaveTextContent("2 个");
    expect(screen.getByText("当前在招").nextElementSibling).toHaveTextContent("1 个");
  });

  it("formats a salary with only a maximum as an upper bound", async () => {
    const cappedJob = { ...job, salary_min_monthly: null };
    renderCompanyDetail((input) => String(input).includes("/jobs")
      ? response(jobsPage([cappedJob]))
      : response(company));

    expect(await screen.findByText(/最高 60,000 元\/月 · 16 薪/)).toBeInTheDocument();
  });

  it("shows an accessible empty state when the company has no active jobs", async () => {
    renderCompanyDetail((input) => String(input).includes("/jobs")
      ? response(jobsPage([]))
      : response({ ...company, job_count: 0 }));

    const empty = await screen.findByRole("status", { name: "暂无在招职位" });
    expect(empty).toHaveAttribute("aria-live", "polite");
  });

  it("loads the next job page without refetching company detail", async () => {
    const requests: string[] = [];
    renderCompanyDetail((input) => {
      const url = String(input);
      requests.push(url);
      if (!url.includes("/jobs")) return response(company);
      return response(jobsPage([{ ...job, title: url.includes("page=2") ? "Research Intern" : job.title }], url.includes("page=2") ? 2 : 1, 11));
    });

    await screen.findByText("Large Model Algorithm Engineer");
    await userEvent.click(screen.getByRole("button", { name: "下一页职位" }));

    expect(await screen.findByText("Research Intern")).toBeInTheDocument();
    await waitFor(() => expect(requests.filter((url) => !url.includes("/jobs"))).toHaveLength(1));
  });
});
