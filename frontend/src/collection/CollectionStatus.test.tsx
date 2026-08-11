import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BrowserRouter, Route, Routes, useLocation } from "react-router-dom";

import type { CompanyListItem, CollectionRequest } from "../api/types";
import { createCollectionRegistry, normalizeCollectionQuery, type CollectionRegistry } from "./polling";
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
  recruiting_coverage: { status: "entry_discovery_pending", active_job_count: null, last_checked_at: null, last_successful_at: null, freshness: "unknown", reason_code: null },
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

function renderSearch(query = "Example company", collectionRegistry: CollectionRegistry = createCollectionRegistry()) {
  window.history.replaceState({}, "", `/companies?q=${encodeURIComponent(query)}`);
  return render(
    <BrowserRouter>
      <Routes>
        <Route path="/companies" element={<SearchPage collectionRegistry={collectionRegistry} />} />
        <Route path="/companies/:companyId" element={<Location />} />
      </Routes>
    </BrowserRouter>,
  );
}

function renderStrictSearch(query = "Example company", collectionRegistry: CollectionRegistry = createCollectionRegistry()) {
  window.history.replaceState({}, "", `/companies?q=${encodeURIComponent(query)}`);
  return render(
    <StrictMode>
      <BrowserRouter>
        <Routes><Route path="/companies" element={<SearchPage collectionRegistry={collectionRegistry} />} /></Routes>
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
  it("uses backend-compatible NFKC, full case-folding, and Unicode-whitespace query keys", () => {
    expect(normalizeCollectionQuery("Straße")).toBe("strasse");
    expect(normalizeCollectionQuery("Ｆｏｏ\u2003ＢＡＲ")).toBe("foobar");
  });

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

  it("caps subsequent automatic polling intervals at ten seconds", async () => {
    vi.useFakeTimers();
    const pollTimes: number[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      if (url.pathname === "/api/v1/collection-requests") return response(collection("queued"), 202);
      pollTimes.push(Date.now());
      return response(collection("queued"));
    }));

    renderSearch("Capped polling");
    await flush();
    await act(() => vi.advanceTimersByTimeAsync(2_000));
    await act(() => vi.advanceTimersByTimeAsync(4_000));
    await act(() => vi.advanceTimersByTimeAsync(8_000));
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    await act(() => vi.advanceTimersByTimeAsync(10_000));

    expect(pollTimes.slice(1).map((time, index) => time - pollTimes[index])).toEqual([4_000, 8_000, 10_000, 10_000]);
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

    expect(screen.getByText("采集仍在进行中")).toBeInTheDocument();
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

  it("keeps a collection submission alive when the page unmounts", async () => {
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
    expect(signal?.aborted).toBe(false);
  });

  it("keeps the prior submission alive when the query changes", async () => {
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

    expect(signals[0].aborted).toBe(false);
  });

  it("does not repeat POST when the search page rerenders", async () => {
    const submissions: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      if (url.pathname === "/api/v1/collection-requests") submissions.push(String(init?.body));
      return response(collection("queued"), 202);
    }));

    const registry = createCollectionRegistry();
    const view = renderSearch("Example company", registry);
    await flush();
    view.rerender(
      <BrowserRouter>
        <Routes><Route path="/companies" element={<SearchPage collectionRegistry={registry} />} /></Routes>
      </BrowserRouter>,
    );
    await flush();

    expect(submissions).toEqual([JSON.stringify({ query: "Example company" })]);
  });

  it("recovers collection submission after a StrictMode effect replay cancels the first request", async () => {
    let submissions = 0;
    let resolveDurablePost!: () => void;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      if (url.pathname !== "/api/v1/collection-requests") return response(collection("queued"));
      submissions += 1;
      return new Promise<Response>((resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
        resolveDurablePost = () => resolve(new Response(JSON.stringify(collection("queued")), { status: 202 }));
      });
    }));

    renderStrictSearch();
    await flush();

    expect(submissions).toBe(1);
    await act(async () => resolveDurablePost());
    expect(screen.getByText("正在排队")).toBeInTheDocument();
  });

  it("times out and aborts a stalled collection submission after two minutes", async () => {
    vi.useFakeTimers();
    let signal: AbortSignal | undefined;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      signal = init?.signal ?? undefined;
      return new Promise<Response>(() => undefined);
    }));

    renderSearch("Stalled POST");
    await flush();
    await act(() => vi.advanceTimersByTimeAsync(120_000));

    expect(screen.getByText("采集仍在进行中")).toBeInTheDocument();
    expect(signal?.aborted).toBe(true);
  });

  it("ignores a collection submission that resolves after its deadline", async () => {
    vi.useFakeTimers();
    let resolvePost!: () => void;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      return new Promise<Response>((resolve) => {
        resolvePost = () => resolve(new Response(JSON.stringify(collection("succeeded", {
          company_id: "company-1",
          completed_at: "2026-08-04T00:01:00Z",
        })), { status: 202 }));
      });
    }));

    renderSearch("Late POST");
    await flush();
    await act(() => vi.advanceTimersByTimeAsync(120_000));
    await act(async () => resolvePost());

    expect(screen.getByText("采集仍在进行中")).toBeInTheDocument();
    expect(screen.queryByTestId("location")).not.toBeInTheDocument();
  });

  it("times out and aborts a stalled database status read", async () => {
    vi.useFakeTimers();
    let readSignal: AbortSignal | undefined;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      if (url.pathname === "/api/v1/collection-requests") return response(collection("queued"), 202);
      readSignal = init?.signal ?? undefined;
      return new Promise<Response>(() => undefined);
    }));

    renderSearch("Stalled GET");
    await flush();
    await act(() => vi.advanceTimersByTimeAsync(2_000));
    await act(() => vi.advanceTimersByTimeAsync(118_000));

    expect(screen.getByText("采集仍在进行中")).toBeInTheDocument();
    expect(readSignal?.aborted).toBe(true);
  });

  it("does not submit equivalent NFKC, case, and whitespace queries twice", async () => {
    vi.useFakeTimers();
    const submitted: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      if (url.pathname === "/api/v1/collection-requests") submitted.push(JSON.parse(String(init?.body)).query);
      return response(collection("queued"), 202);
    }));

    renderSearch("Ｆｏｏ　ＢＡＲ");
    await flush();
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索公司" }), { target: { value: " fOo\tbar " } });
    await act(() => vi.advanceTimersByTimeAsync(250));
    await flush();

    expect(submitted).toEqual(["Ｆｏｏ　ＢＡＲ"]);
  });

  it("does not submit case-fold-equivalent Unicode queries twice", async () => {
    vi.useFakeTimers();
    const submitted: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      if (url.pathname === "/api/v1/collection-requests") submitted.push(JSON.parse(String(init?.body)).query);
      return response(collection("queued"), 202);
    }));

    renderSearch("Straße\u2003");
    await flush();
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索公司" }), { target: { value: "ＳＴＲＡＳＳＥ" } });
    await act(() => vi.advanceTimersByTimeAsync(250));
    await flush();

    expect(submitted).toEqual(["Straße"]);
  });

  it("recreates an expired terminal session when the same query is revisited after its TTL", async () => {
    vi.useFakeTimers();
    const submitted: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      if (url.pathname === "/api/v1/collection-requests") submitted.push(JSON.parse(String(init?.body)).query);
      return response(collection("partial"), 202);
    }));

    const registry = createCollectionRegistry({ sessionTtlMs: 1_000 });
    const first = renderSearch("Expired terminal", registry);
    await flush();
    first.unmount();
    await act(() => vi.advanceTimersByTimeAsync(1_001));
    renderSearch("Expired terminal", registry);
    await flush();

    expect(submitted).toEqual(["Expired terminal", "Expired terminal"]);
  });

  it("rejects a new collection request at capacity without evicting active sessions", async () => {
    vi.useFakeTimers();
    const submitted: string[] = [];
    const signals: AbortSignal[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      if (url.pathname === "/api/v1/collection-requests") {
        submitted.push(JSON.parse(String(init?.body)).query);
        signals.push(init?.signal as AbortSignal);
        return response(collection("queued"), 202);
      }
      return response(collection("queued"));
    }));

    const registry = createCollectionRegistry({ maxSessions: 2 });
    renderSearch("Capacity one", registry);
    await flush();
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索公司" }), { target: { value: "Capacity two" } });
    await act(() => vi.advanceTimersByTimeAsync(250));
    await flush();
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索公司" }), { target: { value: "Capacity three" } });
    await act(() => vi.advanceTimersByTimeAsync(250));
    await flush();

    expect(submitted).toEqual(["Capacity one", "Capacity two"]);
    expect(signals.map((signal) => signal.aborted)).toEqual([false, false]);
    expect(screen.getByText("采集服务暂不可用，请稍后再试")).toBeInTheDocument();
  });

  it("reuses a collection request after the search route remounts", async () => {
    const submitted: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      if (url.pathname === "/api/v1/collection-requests") submitted.push(String(init?.body));
      return response(collection("queued"), 202);
    }));

    const registry = createCollectionRegistry();
    const first = renderSearch("Route remount", registry);
    await flush();
    first.unmount();
    renderSearch("Route remount", registry);
    await flush();

    expect(submitted).toHaveLength(1);
  });

  it("performs one manual status read without restarting polling", async () => {
    vi.useFakeTimers();
    let reads = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      if (url.pathname === "/api/v1/collection-requests") return response(collection("queued"), 202);
      reads += 1;
      return response(collection("queued"));
    }));

    renderSearch("Manual one shot");
    await flush();
    await act(() => vi.advanceTimersByTimeAsync(120_000));
    fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
    await flush();
    const readsAfterRefresh = reads;
    await act(() => vi.advanceTimersByTimeAsync(20_000));

    expect(readsAfterRefresh).toBeGreaterThan(0);
    expect(reads).toBe(readsAfterRefresh);
    expect(screen.getByRole("button", { name: "刷新状态" })).toBeEnabled();
  });

  it("preserves a manual terminal result across a route remount", async () => {
    vi.useFakeTimers();
    let reads = 0;
    const requests: Array<{ path: string; method: string }> = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      requests.push({ path: url.pathname, method: init?.method ?? "GET" });
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      if (url.pathname === "/api/v1/collection-requests") return response(collection("queued"), 202);
      reads += 1;
      if (reads === 1) {
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
        });
      }
      return response(collection("partial", { completed_at: "2026-08-04T00:01:00Z" }));
    }));

    const registry = createCollectionRegistry();
    const first = renderSearch("Manual terminal", registry);
    await flush();
    await act(() => vi.advanceTimersByTimeAsync(120_000));
    fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
    await flush();
    first.unmount();
    renderSearch("Manual terminal", registry);
    await flush();

    expect(screen.getByText("已完成部分资料采集")).toBeInTheDocument();
    expect(reads).toBe(2);
    expect(requests.filter((request) => request.method === "POST")).toHaveLength(1);
  });

  it("preserves a manual active result as manual-only across a route remount", async () => {
    vi.useFakeTimers();
    let reads = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      if (url.pathname === "/api/v1/collection-requests") return response(collection("queued"), 202);
      reads += 1;
      if (reads === 1) {
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
        });
      }
      return response(collection("running"));
    }));

    const registry = createCollectionRegistry();
    const first = renderSearch("Manual active", registry);
    await flush();
    await act(() => vi.advanceTimersByTimeAsync(120_000));
    fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
    await flush();
    first.unmount();
    renderSearch("Manual active", registry);
    await flush();
    await act(() => vi.advanceTimersByTimeAsync(20_000));

    expect(screen.getByText("采集仍在进行中")).toBeInTheDocument();
    expect(reads).toBe(2);
  });

  it("ignores a database status read that resolves after its deadline", async () => {
    vi.useFakeTimers();
    let resolveRead!: () => void;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      if (url.pathname === "/api/v1/collection-requests") return response(collection("queued"), 202);
      return new Promise<Response>((resolve) => {
        resolveRead = () => resolve(new Response(JSON.stringify(collection("succeeded", {
          company_id: "company-1",
          completed_at: "2026-08-04T00:01:00Z",
        }))));
      });
    }));

    renderSearch("Late GET");
    await flush();
    await act(() => vi.advanceTimersByTimeAsync(2_000));
    await act(() => vi.advanceTimersByTimeAsync(118_000));
    await act(async () => resolveRead());

    expect(screen.getByText("采集仍在进行中")).toBeInTheDocument();
    expect(screen.queryByTestId("location")).not.toBeInTheDocument();
  });

  it("retains the last database state and offers refresh after a transient status-read failure", async () => {
    vi.useFakeTimers();
    let reads = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      if (url.pathname === "/api/v1/collection-requests") return response(collection("queued"), 202);
      reads += 1;
      return reads === 1
        ? response({ error: { code: "upstream_unavailable" } }, 503)
        : response(collection("running"));
    }));

    renderSearch("Transient GET");
    await flush();
    await act(() => vi.advanceTimersByTimeAsync(2_000));

    expect(screen.getByText("正在排队")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新状态" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
    await flush();
    expect(screen.getByText("采集仍在进行中")).toBeInTheDocument();
  });

  it("keeps a pre-timeout manual terminal recovery after the original deadline and remount", async () => {
    vi.useFakeTimers();
    let reads = 0;
    let submissions = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/companies") return response(emptyResults());
      if (url.pathname === "/api/v1/collection-requests") {
        submissions += 1;
        return response(collection("queued"), 202);
      }
      reads += 1;
      return reads === 1
        ? response({ error: { code: "upstream_unavailable" } }, 503)
        : response(collection("partial", { completed_at: "2026-08-04T00:01:00Z" }));
    }));

    const registry = createCollectionRegistry();
    const first = renderSearch("Pre-timeout manual terminal", registry);
    await flush();
    await act(() => vi.advanceTimersByTimeAsync(2_000));
    expect(screen.getByText("正在排队")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
    await flush();
    expect(screen.getByText("已完成部分资料采集")).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(118_000));
    expect(screen.getByText("已完成部分资料采集")).toBeInTheDocument();
    expect(reads).toBe(2);
    expect(submissions).toBe(1);

    first.unmount();
    renderSearch("Pre-timeout manual terminal", registry);
    await flush();
    expect(screen.getByText("已完成部分资料采集")).toBeInTheDocument();
    expect(reads).toBe(2);
    expect(submissions).toBe(1);
  });
});
