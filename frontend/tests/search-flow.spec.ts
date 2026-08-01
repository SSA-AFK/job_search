import { expect, test, type Page, type Route } from "@playwright/test";

const companyId = "11111111-1111-4111-8111-111111111111";
const readinessTimeout = 20_000;

const companyItem = {
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
};

const companyDetail = {
  ...companyItem,
  aliases: ["深度求索"],
  filings: [],
  sources: [],
  job_count: 0,
};

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function mockApi(page: Page, collectionRequests: string[]) {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/collection-requests" && request.method() === "POST") {
      collectionRequests.push(request.postData() ?? "");
      return json(route, {
        error: {
          code: "collection_unavailable",
          message: "Collection service is unavailable.",
        },
      }, 503);
    }
    if (url.pathname === `/api/v1/companies/${companyId}/jobs`) {
      return json(route, { items: [], page: 1, page_size: 10, total: 0 });
    }
    if (url.pathname === `/api/v1/companies/${companyId}`) return json(route, companyDetail);
    if (url.pathname === "/api/v1/companies") {
      const isMissing = url.searchParams.get("q") === "不存在公司";
      return json(route, {
        items: isMissing ? [] : [companyItem],
        page: Number(url.searchParams.get("page") ?? "1"),
        page_size: Number(url.searchParams.get("page_size") ?? "20"),
        total: isMissing ? 0 : 1,
      });
    }
    return json(route, { error: { code: "not_found", message: "Resource not found" } }, 404);
  });
}

test("search URL state navigates to the company detail route", async ({ page }) => {
  await mockApi(page, []);
  await page.goto("/companies");

  const companyLink = page.getByRole("link", { name: "DeepSeek", exact: true });
  await expect(companyLink).toBeVisible({ timeout: readinessTimeout });
  await page.getByRole("searchbox", { name: "搜索公司" }).fill("DeepSeek");
  await expect(page).toHaveURL(/\/companies\?q=DeepSeek/, { timeout: readinessTimeout });
  await expect(companyLink).toBeVisible({ timeout: readinessTimeout });
  await companyLink.click();

  await expect(page).toHaveURL(`/companies/${companyId}`, { timeout: readinessTimeout });
  await expect(page.getByRole("heading", { name: "DeepSeek", level: 1 })).toBeVisible({
    timeout: readinessTimeout,
  });
  await expect(page.getByRole("status", { name: "暂无在招职位" })).toBeVisible({
    timeout: readinessTimeout,
  });
});

test("an empty query submits one terminal collection request", async ({ page }) => {
  const collectionRequests: string[] = [];
  await mockApi(page, collectionRequests);
  await page.goto("/companies?q=%20%20不存在公司%20%20");

  await expect(page.getByText("暂未收录这家公司")).toBeVisible({
    timeout: readinessTimeout,
  });
  await expect(page.getByText("采集服务暂不可用，请稍后再试")).toBeVisible({
    timeout: readinessTimeout,
  });
  await expect.poll(() => collectionRequests, { timeout: readinessTimeout }).toHaveLength(1);
  expect(JSON.parse(collectionRequests[0])).toEqual({ query: "不存在公司" });
  expect(collectionRequests).toHaveLength(1);
});
