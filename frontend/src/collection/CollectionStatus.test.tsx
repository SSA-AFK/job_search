import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BrowserRouter, Route, Routes, useLocation } from "react-router-dom";

import type { CompanyListItem, CollectionRequest } from "../api/types";
import { SearchPage } from "../search/SearchPage";

const company: CompanyListItem = {
  id: "company-1",
  canonical_name: "Existing company",
  industry: null,
  sub_industry: null,
  funding_stage: "unknown",
  scale: "unknown",
  city: null,
  logo_url: null,
  website: null,
  description: null,
  last_collected_at: null,
  created_at: "2026-08-04T00:00:00Z",
  updated_at: "2026-08-04T00:00:00Z",
};

function collection(status: CollectionRequest["status"], overrides: Partial<CollectionRequest> = {}): CollectionRequest {
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

function response(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  }));
}

function emptyResults() {
  return { items: [], page: 1, page_size: 20, total: 0 };
}

function Location() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}</output>;
}

function renderSearch(query = "Example company") {
  window.history.replaceState({}, "", `/companies?q=${encodeURIComponent(query)}`);
  return render(
    <BrowserRouter>
      <Routes>
        <Route path="/companies" element={<SearchPage />} />
        <Route path="/companies/:companyId" element={<Location />} />
      </Routes>
    </BrowserRouter>,
  );
}

function renderStrictSearch(query = "Example company") {
  window.history.replaceState({}, "", `/companies?q=${encodeURIComponent(query)}`);
  return render(
    <StrictMode>
      <BrowserRouter>
        <Routes><Route path="/companies" element={<SearchPage />} /></Routes>
      </BrowserRouter>
    </StrictMode>,
  );
}

async function flush() {
  await act(async () => undefined);
}

afterEach(() => {
  vi.useRealTimers();
  cleanup();
  vi.unstubAllGlobals();
});

describe("CollectionStatus", () => {
  it("polls with capped backoff and navigates after success", async () => {
    vi.useFakeTimers();
    let polls = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      if (url.pathname === "/api/v1/collection-requests") return response(collection("queued"), 202);
      polls += 1;
      return response(polls === 1
        ? collection("running")
        : collection("succeeded", { company_id: "company-1", completed_at: "2026-08-04T00:01:00Z" }));
    }));

    renderSearch();
    await flush();
    expect(screen.getByText("正在排队")).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(2_000));
    expect(screen.getByText("正在采集")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(4_000));
    expect(screen.getByTestId("location")).toHaveTextContent("/companies/company-1");
  });

  it("shows a partial result without continuing to poll", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), window.location.origin);
      return url.pathname === "/api/v1/companies"
        ? response(emptyResults())
        : response(collection("partial", { completed_at: "2026-08-04T00:01:00Z" }), 202);
    }));

    renderSearch();
    await flush();
    expect(screen.getByText("已完成部分资料采集")).toBeInTheDocument();
  });

  it("shows only the public message for a failed collection", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), window.location.origin);
      return url.pathname === "/api/v1/companies"
        ? response(emptyResults())
        : response(collection("failed", {
          error_code: "collection_unavailable",
          completed_at: "2026-08-04T00:01:00Z",
        }), 202);
    }));

    renderSearch();
    await flush();
    expect(screen.getByText("采集服务暂不可用，请稍后再试")).toBeInTheDocument();
    expect(screen.queryByText(/internal database exception/i)).not.toBeInTheDocument();
  });

  it("stops automatically after two minutes", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), window.location.origin);
      return url.pathname === "/api/v1/companies"
        ? response(emptyResults())
        : response(collection("queued"), url.pathname === "/api/v1/collection-requests" ? 202 : 200);
    }));

    renderSearch();
    await flush();
    await act(() => vi.advanceTimersByTimeAsync(120_000));
    expect(screen.getByText("采集仍在进行中")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新状态" })).toBeEnabled();
  });

  it("manually refreshes the existing request after timeout without resubmitting", async () => {
    vi.useFakeTimers();
    const requests: Array<{ path: string; method: string }> = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      requests.push({ path: url.pathname, method: init?.method ?? "GET" });
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      if (url.pathname === "/api/v1/collection-requests") return response(collection("queued"), 202);
      return response(collection("queued"));
    }));

    renderSearch();
    await flush();
    await act(() => vi.advanceTimersByTimeAsync(120_000));
    fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
    await flush();

    expect(screen.getByText("正在排队")).toBeInTheDocument();
    expect(requests.filter((request) => request.path === "/api/v1/collection-requests" && request.method === "POST")).toHaveLength(1);
  });

  it("reuses a request when a normalized query becomes active again", async () => {
    vi.useFakeTimers();
    const submitted: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      if (url.pathname === "/api/v1/collection-requests") {
        submitted.push(JSON.parse(String(init?.body)).query);
        return response(collection("queued"), 202);
      }
      return response(collection("queued"));
    }));

    renderSearch("  Example company  ");
    await flush();
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索公司" }), { target: { value: "Another company" } });
    await act(() => vi.advanceTimersByTimeAsync(250));
    await flush();
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索公司" }), { target: { value: " Example company " } });
    await act(() => vi.advanceTimersByTimeAsync(250));
    await flush();

    expect(submitted).toEqual(["Example company", "Another company"]);
  });

  it("aborts the collection request when the page unmounts", async () => {
    let signal: AbortSignal | undefined;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      signal = init?.signal ?? undefined;
      return new Promise<Response>(() => undefined);
    }));

    const view = renderSearch();
    await flush();
    view.unmount();
    expect(signal?.aborted).toBe(true);
  });

  it("aborts a pending collection request when the query changes", async () => {
    vi.useFakeTimers();
    const signals: AbortSignal[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      signals.push(init?.signal as AbortSignal);
      return new Promise<Response>(() => undefined);
    }));

    renderSearch();
    await flush();
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索公司" }), { target: { value: "Another company" } });
    await act(() => vi.advanceTimersByTimeAsync(250));
    await flush();

    expect(signals[0].aborted).toBe(true);
  });

  it("does not repeat POST when the search page rerenders", async () => {
    const submissions: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      if (url.pathname === "/api/v1/collection-requests") submissions.push(String(init?.body));
      return response(collection("queued"), 202);
    }));

    const view = renderSearch();
    await flush();
    view.rerender(
      <BrowserRouter>
        <Routes><Route path="/companies" element={<SearchPage />} /></Routes>
      </BrowserRouter>,
    );
    await flush();

    expect(submissions).toEqual([JSON.stringify({ query: "Example company" })]);
  });

  it("recovers collection submission after a StrictMode effect replay cancels the first request", async () => {
    let submissions = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      if (url.pathname !== "/api/v1/collection-requests") return response(collection("queued"));
      submissions += 1;
      if (submissions > 1) return response(collection("queued"), 202);
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
      });
    }));

    renderStrictSearch();
    await flush();

    expect(screen.getByText("正在排队")).toBeInTheDocument();
    expect(submissions).toBeGreaterThanOrEqual(2);
  });
});
