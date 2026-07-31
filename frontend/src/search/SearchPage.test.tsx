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
  scale: "100-499",
  city: "北京",
  logo_url: "https://www.deepseek.com/favicon.ico",
  website: "https://www.deepseek.com",
  description: "专注于通用人工智能基础模型研发。",
  last_collected_at: "2026-07-30T08:00:00Z",
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-30T08:00:00Z",
};

const moonshot: CompanyListItem = {
  id: "22222222-2222-4222-8222-222222222222",
  canonical_name: "月之暗面",
  industry: "人工智能",
  sub_industry: "大语言模型",
  funding_stage: "series_c",
  scale: "100-499",
  city: "北京",
  logo_url: null,
  website: "https://www.moonshot.cn",
  description: "面向大众提供长文本智能助手服务。",
  last_collected_at: null,
  created_at: "2026-07-02T00:00:00Z",
  updated_at: "2026-07-29T08:00:00Z",
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
    await userEvent.selectOptions(screen.getByLabelText("融资阶段"), "series_b");

    await waitFor(() => expect(window.location.search).toContain("funding_stage=series_b"));
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(Object.fromEntries(requests[1].searchParams)).toEqual({
      q: "deepseek",
      city: "Beijing",
      funding_stage: "series_b",
      page: "1",
      page_size: "20",
      sort: "relevance",
    });
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

  it("shows a useful empty result", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse(page([]))));
    renderSearchPage("/companies?q=不存在公司");

    expect(await screen.findByText("没有找到符合条件的公司")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "清除全部筛选" })[0]).toBeEnabled();
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
