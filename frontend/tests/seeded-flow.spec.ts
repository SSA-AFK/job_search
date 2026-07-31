import { expect, test } from "@playwright/test";


test("searches the real seed through Vite and opens active job details", async ({ page }) => {
  await page.route("https://**", (route) => route.abort("blockedbyclient"));
  await page.goto("/companies");

  const seededSearch = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/v1/companies" && url.searchParams.get("q") === "DeepSeek";
  });
  await page.getByRole("searchbox", { name: "搜索公司" }).fill("DeepSeek");
  const searchResponse = await seededSearch;

  expect(searchResponse.status()).toBe(200);
  expect(new URL(searchResponse.url()).origin).toBe("http://127.0.0.1:4173");
  const companyLink = page.getByRole("link", {
    name: "DeepSeek（深度求索）",
    exact: true,
  });
  await expect(companyLink).toBeVisible();

  const detailResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return /\/api\/v1\/companies\/[0-9a-f-]{36}$/.test(url.pathname);
  });
  const jobsResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return /\/api\/v1\/companies\/[0-9a-f-]{36}\/jobs$/.test(url.pathname);
  });
  await companyLink.click();

  expect((await detailResponse).status()).toBe(200);
  expect((await jobsResponse).status()).toBe(200);
  await expect(
    page.getByRole("heading", { name: "DeepSeek（深度求索）", level: 1 }),
  ).toBeVisible();
  expect(page.url()).toMatch(/\/companies\/[0-9a-f-]{36}$/);
  await expect(page.getByText("Large Model Algorithm Engineer")).toBeVisible();
  await expect(page.getByText("Research Intern")).toHaveCount(0);
  await expect(page.getByText("职位记录").locator("..")).toContainText("2 个");
  await expect(page.getByText("当前在招").locator("..")).toContainText("1 个");

  const providerLink = page.getByRole("link", { name: "official投递" });
  await expect(providerLink).toHaveAttribute(
    "href",
    "https://www.deepseek.com/careers/llm-001",
  );
  await expect(providerLink).toHaveAttribute("target", "_blank");
  await expect(providerLink).toHaveAttribute("rel", "noreferrer");
});
