import { expect, test, type Page, type Route } from "@playwright/test";

const companyId = "11111111-1111-4111-8111-111111111111";

type Scenario = "success" | "partial" | "failed" | "timeout";

function collection(status: "queued" | "running" | "partial" | "succeeded" | "failed", overrides = {}) {
  return {
    id: "request-1",
    query: "Example company",
    normalized_query: "example company",
    status,
    company_id: null,
    error_code: null,
    completed_at: null,
    created_at: "2026-08-04T00:00:00Z",
    updated_at: "2026-08-04T00:00:00Z",
    ...overrides,
  };
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function mockCollectionApi(page: Page, scenario: { current: Scenario }) {
  let polls = 0;
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/companies") {
      return json(route, { items: [], page: 1, page_size: 20, total: 0 });
    }
    if (url.pathname === "/api/v1/collection-requests" && request.method() === "POST") {
      polls = 0;
      if (scenario.current === "partial") return json(route, collection("partial"), 202);
      if (scenario.current === "failed") {
        return json(route, collection("failed", { error_code: "collection_unavailable" }), 202);
      }
      return json(route, collection("queued"), 202);
    }
    if (url.pathname === "/api/v1/collection-requests/request-1") {
      polls += 1;
      if (scenario.current === "success") {
        return json(route, polls === 1
          ? collection("running")
          : collection("succeeded", { company_id: companyId, completed_at: "2026-08-04T00:01:00Z" }));
      }
      return json(route, collection("queued"));
    }
    return json(route, { error: { code: "not_found", message: "Not found" } }, 404);
  });
}

async function visitCollection(page: Page, query: string) {
  const created = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/v1/collection-requests"
    && response.status() === 202
  ));
  await page.goto(`/companies?q=${encodeURIComponent(query)}`);
  await created;
}

test("collection lifecycle reaches success, partial, failed, and timeout states without layout overflow", async ({ page }, testInfo) => {
  const scenario: { current: Scenario } = { current: "success" };
  await page.addInitScript(() => {
    const nativeNow = Date.now.bind(Date);
    let offset = 0;
    Date.now = () => nativeNow() + offset;
    (window as Window & { advanceCollectionTime: (milliseconds: number) => void }).advanceCollectionTime = (
      milliseconds,
    ) => { offset += milliseconds; };
  });
  await mockCollectionApi(page, scenario);

  await visitCollection(page, "Example company");
  await expect(page.getByText("正在排队")).toBeVisible();
  await expect(page.getByText("正在采集")).toBeVisible({ timeout: 10_000 });
  await expect(page).toHaveURL(`/companies/${companyId}`, { timeout: 10_000 });

  scenario.current = "partial";
  await visitCollection(page, "Example company partial");
  await expect(page.getByText("已完成部分资料采集")).toBeVisible();

  scenario.current = "failed";
  await visitCollection(page, "Example company failed");
  await expect(page.getByText("采集服务暂不可用，请稍后再试")).toBeVisible();

  scenario.current = "timeout";
  await visitCollection(page, "Example company timeout");
  await expect(page.getByText("正在排队")).toBeVisible();
  await page.evaluate(() => {
    (window as Window & { advanceCollectionTime: (milliseconds: number) => void }).advanceCollectionTime(120_000);
  });
  await expect(page.getByText("采集仍在进行中")).toBeVisible();
  await expect(page.getByRole("button", { name: "刷新状态" })).toBeVisible();

  const measurements = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    status: (() => {
      const rect = document.querySelector(".collection-status")?.getBoundingClientRect();
      return rect ? { left: rect.left, right: rect.right } : null;
    })(),
  }));
  expect(measurements.scrollWidth).toBeLessThanOrEqual(measurements.clientWidth);
  expect(measurements.status?.left).toBeGreaterThanOrEqual(0);
  expect(measurements.status?.right).toBeLessThanOrEqual(measurements.clientWidth);
  await page.screenshot({ path: testInfo.outputPath(`collection-status-${testInfo.project.name}.png`), fullPage: true });
});
