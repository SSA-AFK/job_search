import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import type { CompanyListItem } from "../api/types";
import { SearchPage } from "./SearchPage";

const deepSeek: CompanyListItem = {
  id: "11111111-1111-4111-8111-111111111111",
  canonical_name: "DeepSeek",
  industry: "人工智能",
  sub_industry: "大语言模型",
  funding_stage: "series_b",
  scale: "200_to_499",
  city: "北京",
  logo_url: "https://www.deepseek.com/favicon.ico",
  website: "https://www.deepseek.com",
  description: "专注于通用人工智能基础模型研发。",
  last_collected_at: "2026-07-30T08:00:00Z",
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-30T08:00:00Z",
  recruiting_coverage: { status: "active_roles", active_job_count: 2, last_checked_at: "2026-07-30T08:00:00Z", last_successful_at: "2026-07-30T08:00:00Z", freshness: "fresh", reason_code: null },
};

const moonshot: CompanyListItem = {
  id: "22222222-2222-4222-8222-222222222222",
  canonical_name: "月之暗面",
  industry: "人工智能",
  sub_industry: "大语言模型",
  funding_stage: "series_c_plus",
  scale: "500_plus",
  city: "北京",
  logo_url: null,
  website: "https://www.moonshot.cn",
  description: "面向大众提供长文本智能助手服务。",
  last_collected_at: null,
  created_at: "2026-07-02T00:00:00Z",
  updated_at: "2026-07-29T08:00:00Z",
  recruiting_coverage: { status: "entry_discovery_pending", active_job_count: null, last_checked_at: null, last_successful_at: null, freshness: "unknown", reason_code: null },
};

const page = (items: CompanyListItem[] = [deepSeek], currentPage = 1, total = items.length) => ({
  items,
  page: currentPage,
  page_size: 20,
  total,
});

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

function renderSearchPage(route = "/companies") {
  window.history.replaceState({}, "", route);
  return render(
    <BrowserRouter>
      <Routes>
        <Route path="/companies" element={<SearchPage />} />
      </Routes>
    </BrowserRouter>,
  );
}

describe("SearchPage", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse(page())));
  });

  afterEach(() => {
    vi.useRealTimers();
    cleanup();
    vi.unstubAllGlobals();
  });

  it("writes filters to the URL and requests the matching page", async () => {
    const requests: URL[] = [];
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), window.location.origin);
      requests.push(url);
      const item = url.searchParams.get("q") === "deepseek" ? deepSeek : moonshot;
      return jsonResponse(page([item]));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderSearchPage("/companies?q=deepseek&city=Beijing");

    expect(await screen.findByText("DeepSeek")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("融资阶段"), "series_c_plus");

    await waitFor(() => expect(window.location.search).toContain("funding_stage=series_c_plus"));
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(Object.fromEntries(requests[1].searchParams)).toEqual({
      q: "deepseek",
      city: "Beijing",
      funding_stage: "series_c_plus",
      page: "1",
      page_size: "20",
      sort: "relevance",
    });
  });

  it("renders approved funding-stage and scale labels", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse(page([moonshot]))));
    renderSearchPage();

    const row = (await screen.findByText("月之暗面")).closest("li");
    expect(row).toHaveTextContent("C 轮及以后");
    expect(row).toHaveTextContent("500 人以上");
  });

  it("keeps a shared URL value visible when it is not in the preset options", async () => {
    renderSearchPage("/companies?city=北京");

    await screen.findByText("DeepSeek");
    expect(screen.getByLabelText("城市")).toHaveValue("北京");
  });

  it("debounces text search for 250ms before writing the URL", async () => {
    renderSearchPage();
    await screen.findByText("DeepSeek");
    vi.useFakeTimers();

    try {
      fireEvent.change(screen.getByRole("searchbox", { name: "搜索公司" }), {
        target: { value: "kimi" },
      });
      expect(window.location.search).not.toContain("q=kimi");

      await act(() => vi.advanceTimersByTimeAsync(249));
      expect(window.location.search).not.toContain("q=kimi");

      await act(() => vi.advanceTimersByTimeAsync(1));
      expect(window.location.search).toContain("q=kimi");
    } finally {
      vi.useRealTimers();
    }
  });

  it("clearing a relevance query removes the stale URL sort and requests recent updates", async () => {
    const requests: URL[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      requests.push(new URL(String(input), window.location.origin));
      return jsonResponse(page());
    }));
    renderSearchPage("/companies?q=deepseek&sort=relevance");

    await screen.findByText("DeepSeek");
    vi.useFakeTimers();

    try {
      fireEvent.change(screen.getByRole("searchbox", { name: "搜索公司" }), {
        target: { value: "" },
      });
      await act(() => vi.advanceTimersByTimeAsync(250));

      expect(window.location.search).not.toContain("sort=relevance");
      expect(requests).toHaveLength(2);
      expect(screen.getByLabelText("排序")).toHaveValue("updated_at");
      expect(Object.fromEntries(requests[1].searchParams)).toMatchObject({
        page: "1",
        page_size: "20",
        sort: "updated_at",
      });
      expect(requests[1].searchParams.has("q")).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("normalizes a direct queryless relevance URL to recent updates", async () => {
    const requests: URL[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      requests.push(new URL(String(input), window.location.origin));
      return jsonResponse(page());
    }));
    renderSearchPage("/companies?sort=relevance");

    await screen.findByText("DeepSeek");

    expect(window.location.search).not.toContain("sort=relevance");
    expect(screen.getByLabelText("排序")).toHaveValue("updated_at");
    expect(Object.fromEntries(requests[0].searchParams)).toMatchObject({
      page: "1",
      page_size: "20",
      sort: "updated_at",
    });
    expect(requests).toHaveLength(1);
  });

  it("aborts a stale request when URL filters change", async () => {
    const signals: AbortSignal[] = [];
    let requestCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
        signals.push(init?.signal as AbortSignal);
        requestCount += 1;
        if (requestCount === 1) return new Promise<Response>(() => undefined);
        return jsonResponse(page([moonshot]));
      }),
    );
    renderSearchPage();

    await userEvent.selectOptions(screen.getByLabelText("融资阶段"), "series_b");

    expect(await screen.findByText("月之暗面")).toBeInTheDocument();
    expect(signals).toHaveLength(2);
    expect(signals[0].aborted).toBe(true);
    expect(signals[1].aborted).toBe(false);
  });

  it("shows a stable loading state while the request is pending", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => undefined)));
    renderSearchPage();

    expect(screen.getByRole("status", { name: "正在加载公司" })).toBeInTheDocument();
  });

  it("shows an actionable API error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        jsonResponse(
          { error: { code: "internal_error", message: "Internal server error", details: null } },
          500,
        ),
      ),
    );
    renderSearchPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("公司列表加载失败");
    expect(screen.getByRole("button", { name: "重新加载" })).toBeEnabled();
  });

  it("shows a useful filtered empty result without collecting a known query", async () => {
    const fetchMock = vi.fn(() => jsonResponse(page([])));
    vi.stubGlobal("fetch", fetchMock);
    renderSearchPage("/companies?q=DeepSeek&city=Shanghai");

    const emptyHeading = await screen.findByText("没有找到符合条件的公司");
    expect(emptyHeading.closest('[role="status"]')).toHaveAttribute("aria-live", "polite");
    expect(screen.getAllByRole("button", { name: "清除全部筛选" })[0]).toBeEnabled();
    await act(() => new Promise((resolve) => window.setTimeout(resolve, 50)));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows a stable empty state when collection is disabled", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url === "/api/v1/collection-requests") {
        return jsonResponse({
          error: {
            code: "collection_unavailable",
            message: "Collection service is unavailable.",
          },
        }, 503);
      }
      return jsonResponse(page([]));
    }));
    renderSearchPage("/companies?q=%20%20不存在公司%20%20");

    expect(await screen.findByText("暂未收录这家公司")).toBeInTheDocument();
    expect(await screen.findByText("采集服务暂不可用，请稍后再试")).toBeInTheDocument();
    await waitFor(() => expect(requests.filter(({ url }) => url === "/api/v1/collection-requests")).toHaveLength(1));
    expect(requests.find(({ url }) => url === "/api/v1/collection-requests")?.init).toMatchObject({
      method: "POST",
      body: JSON.stringify({ query: "不存在公司" }),
    });
  });

  it("keeps collection outcomes isolated when empty-query responses arrive out of order", async () => {
    let resolveFirstCollection!: (response: Response) => void;
    const collectionUnavailable = () => new Response(JSON.stringify({
      error: {
        code: "collection_unavailable",
        message: "Collection service is unavailable.",
      },
    }), { status: 503, headers: { "Content-Type": "application/json" } });

    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) !== "/api/v1/collection-requests") return jsonResponse(page([]));
      const query = JSON.parse(String(init?.body)).query;
      if (query === "第一家公司") {
        return new Promise<Response>((resolve) => { resolveFirstCollection = resolve; });
      }
      return Promise.resolve(collectionUnavailable());
    }));
    renderSearchPage("/companies?q=第一家公司");
    expect(await screen.findByText("正在确认是否可以补充资料")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("searchbox", { name: "搜索公司" }), {
      target: { value: "第二家公司" },
    });
    await waitFor(() => expect(window.location.search).toContain(encodeURIComponent("第二家公司")));
    expect(await screen.findByText("采集服务暂不可用，请稍后再试")).toBeInTheDocument();

    await act(async () => resolveFirstCollection(collectionUnavailable()));
    expect(screen.getByText("采集服务暂不可用，请稍后再试")).toBeInTheDocument();
    expect(screen.queryByText("没有找到符合条件的公司")).not.toBeInTheDocument();
  });

  it("offers a valid-page recovery when the requested page is out of range", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = new URL(String(input), window.location.origin);
        const currentPage = Number(url.searchParams.get("page") ?? "1");
        return currentPage === 999
          ? jsonResponse(page([], 999, 21))
          : jsonResponse(page([moonshot], currentPage, 21));
      }),
    );
    renderSearchPage("/companies?q=deepseek&page=999");

    const recoveryHeading = await screen.findByText("当前页超出结果范围");
    expect(recoveryHeading.closest('[role="status"]')).toHaveAttribute("aria-live", "polite");
    await userEvent.click(screen.getByRole("button", { name: "返回第 2 页" }));

    expect(await screen.findByText("月之暗面")).toBeInTheDocument();
    expect(window.location.search).toContain("page=2");
  });

  it("announces the populated result count as a polite live status", async () => {
    renderSearchPage();

    await screen.findByText("DeepSeek");
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveTextContent("共 1 家");
  });

  it("does not render an executable website URL", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => jsonResponse(page([{ ...deepSeek, website: "javascript:alert(document.domain)" }]))),
    );
    renderSearchPage();

    await screen.findByText("DeepSeek");
    expect(screen.queryByRole("link", { name: "访问 DeepSeek 官网" })).not.toBeInTheDocument();
    expect(screen.getByText("官网待确认")).toBeInTheDocument();
  });

  it.each([
    ["non-HTTP", "data:image/svg+xml,<svg></svg>"],
    ["credential-bearing", "https://user:password@example.com/logo.png"],
  ])("falls back to initials for a %s list logo", async (_case, logoUrl) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => jsonResponse(page([{ ...deepSeek, logo_url: logoUrl }]))),
    );
    renderSearchPage();

    await screen.findByText("DeepSeek");
    expect(document.querySelector("img.company-logo")).not.toBeInTheDocument();
    expect(document.querySelector(".company-logo.logo-fallback")).toHaveTextContent("DE");
  });

  it("clears every active filter and returns to the first page", async () => {
    renderSearchPage("/companies?q=deepseek&city=北京&page=3&sort=name");
    await screen.findByText("DeepSeek");

    await userEvent.click(screen.getByRole("button", { name: "清除全部筛选" }));

    await waitFor(() => expect(window.location.pathname + window.location.search).toBe("/companies"));
    expect(screen.getByRole("searchbox", { name: "搜索公司" })).toHaveValue("");
  });

  it("changes pages through compact pagination controls", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = new URL(String(input), window.location.origin);
        const currentPage = Number(url.searchParams.get("page") ?? "1");
        return jsonResponse(page(currentPage === 2 ? [moonshot] : [deepSeek], currentPage, 21));
      }),
    );
    renderSearchPage();
    await screen.findByText("DeepSeek");

    await userEvent.click(screen.getByRole("button", { name: "下一页" }));

    expect(await screen.findByText("月之暗面")).toBeInTheDocument();
    expect(window.location.search).toContain("page=2");
  });

  it("restores controls and results when the browser goes back", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = new URL(String(input), window.location.origin);
        const hasCity = url.searchParams.get("city") === "Beijing";
        return jsonResponse(page(hasCity ? [moonshot] : [deepSeek]));
      }),
    );
    renderSearchPage();
    await screen.findByText("DeepSeek");

    await userEvent.selectOptions(screen.getByLabelText("城市"), "Beijing");
    expect(await screen.findByText("月之暗面")).toBeInTheDocument();

    window.history.back();
    await waitFor(() => expect(window.location.search).toBe(""));
    expect(await screen.findByText("DeepSeek")).toBeInTheDocument();
    expect(screen.getByLabelText("城市")).toHaveValue("");
  });
});
