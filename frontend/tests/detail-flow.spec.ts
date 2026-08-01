import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test, type Page, type Route } from "@playwright/test";

const companyId = "11111111-1111-4111-8111-111111111111";
const visualDirectory = resolve(
  process.cwd(),
  "../.superpowers/sdd/2026-07-31-company-search-web-foundation/task-7-visuals",
);

const detail = {
  id: companyId,
  canonical_name: "DeepSeek",
  industry: "Artificial Intelligence",
  sub_industry: "Foundation Models",
  funding_stage: "unknown",
  scale: "200_to_499",
  city: "Hangzhou",
  logo_url: null,
  website: "https://www.deepseek.com/",
  description: "专注于通用人工智能基础模型研发。",
  last_collected_at: "2026-07-30T08:00:00Z",
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-30T08:00:00Z",
  aliases: ["深度求索"],
  job_count: 11,
  filings: [{
    filing_type: "icp",
    filing_number: "浙ICP备2023025841号",
    filing_name: "DeepSeek official website",
    filing_authority: "Ministry of Industry and Information Technology",
    filing_date: "2023-08-01",
    filing_status: "active",
    detail_url: "https://beian.miit.gov.cn/",
  }],
  sources: [{
    provider: "official_registry",
    url: "https://registry.example.com/deepseek",
    title: "DeepSeek registry record",
    covered_fields: ["canonical_name", "website"],
    confidence: "0.975",
    published_at: "2026-06-01T00:00:00Z",
    fetched_at: "2026-07-31T00:00:00Z",
  }],
};

function job(title: string) {
  return {
    id: title === "Research Intern" ? "33333333-3333-4333-8333-333333333333" : "22222222-2222-4222-8222-222222222222",
    company_id: companyId,
    title,
    job_type: title === "Research Intern" ? "internship" : "full_time",
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
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function mockDetailApi(page: Page) {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === `/api/v1/companies/${companyId}/jobs`) {
      const currentPage = Number(url.searchParams.get("page") ?? "1");
      return json(route, {
        items: [job(currentPage === 2 ? "Research Intern" : "Large Model Algorithm Engineer")],
        page: currentPage,
        page_size: 10,
        total: 11,
      });
    }
    if (url.pathname === `/api/v1/companies/${companyId}`) return json(route, detail);
    return json(route, { error: { code: "not_found", message: "Resource not found" } }, 404);
  });
}

test("detail keeps source labels paired, paginates jobs, and has no horizontal overflow", async ({ page }, testInfo) => {
  await mockDetailApi(page);
  await page.goto(`/companies/${companyId}`);

  await expect(page.getByRole("heading", { name: "DeepSeek", level: 1 })).toBeVisible({
    timeout: 20_000,
  });
  const official = page.getByRole("link", { name: "公司官网投递" });
  await expect(official).toHaveAttribute("href", "https://example.com/jobs/1");
  await expect(official).toHaveAttribute("target", "_blank");
  await expect(official).toHaveAttribute("rel", "noreferrer");
  await expect(page.getByRole("link", { name: "知乎投递" })).toHaveAttribute("href", "https://jobs.example.com/1");
  await expect(page.getByRole("link", { name: "unsafe投递" })).toHaveCount(0);

  await page.getByRole("button", { name: "下一页职位" }).click();
  await expect(page.getByText("Research Intern")).toBeVisible({ timeout: 20_000 });

  const measurements = await page.evaluate(() => {
    const documentElement = document.documentElement;
    const selectors = [".detail-identity", ".record-list", ".job-list", ".application-links"];
    return {
      viewportWidth: window.innerWidth,
      documentClientWidth: documentElement.clientWidth,
      documentScrollWidth: documentElement.scrollWidth,
      boxes: Object.fromEntries(selectors.map((selector) => {
        const rect = document.querySelector(selector)?.getBoundingClientRect();
        return [selector, rect ? { left: rect.left, right: rect.right, width: rect.width } : null];
      })),
    };
  });
  expect(measurements.documentScrollWidth).toBeLessThanOrEqual(measurements.documentClientWidth);
  for (const box of Object.values(measurements.boxes)) {
    expect(box?.left).toBeGreaterThanOrEqual(0);
    expect(box?.right).toBeLessThanOrEqual(measurements.viewportWidth);
  }

  mkdirSync(visualDirectory, { recursive: true });
  const screenshotPath = resolve(visualDirectory, `company-detail-${testInfo.project.name}.png`);
  await page.screenshot({ path: screenshotPath, fullPage: true });
  console.log(`TASK7_LAYOUT ${testInfo.project.name} ${JSON.stringify(measurements)}`);
  console.log(`TASK7_SCREENSHOT ${screenshotPath}`);
});
