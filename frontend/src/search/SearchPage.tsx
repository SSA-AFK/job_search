import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import type { CompanyListItem, Page } from "../api/types";
import { CollectionStatus } from "../collection/CollectionStatus";
import { type CollectionRegistry } from "../collection/polling";
import { CompanyResults } from "./CompanyResults";
import { Filters } from "./Filters";
import {
  readCompanySearchParams,
  type SearchParamKey,
  withPage,
  withSearchParam,
} from "./search-params";

export function SearchPage({ collectionRegistry }: { collectionRegistry?: CollectionRegistry } = {}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const serializedParams = searchParams.toString();
  const params = useMemo(
    () => readCompanySearchParams(new URLSearchParams(serializedParams)),
    [serializedParams],
  );
  const [searchValue, setSearchValue] = useState(params.q ?? "");
  const [data, setData] = useState<Page<CompanyListItem> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  useEffect(() => {
    setSearchValue(params.q ?? "");
  }, [params.q]);

  useEffect(() => {
    if (params.q || searchParams.get("sort") !== "relevance") return;
    const next = new URLSearchParams(searchParams);
    next.delete("sort");
    setSearchParams(next, { replace: true });
  }, [params.q, searchParams, setSearchParams]);

  useEffect(() => {
    if (searchValue.trim() === (params.q ?? "")) return;
    const timeout = window.setTimeout(() => {
      setSearchParams(withSearchParam(searchParams, "q", searchValue), { replace: true });
    }, 250);
    return () => window.clearTimeout(timeout);
  }, [params.q, searchParams, searchValue, setSearchParams]);

  useEffect(() => {
    if (!params.q && searchParams.get("sort") === "relevance") return;
    const controller = new AbortController();
    setLoading(true);
    setError(false);
    setData(null);

    api.getCompanies(params, controller.signal)
      .then((result) => {
        setData(result);
        setLoading(false);
      })
      .catch((requestError: unknown) => {
        if (requestError instanceof DOMException && requestError.name === "AbortError") return;
        setError(true);
        setLoading(false);
      });

    return () => controller.abort();
  }, [serializedParams, retryCount]);

  const hasActiveFilters = [
    params.q,
    params.industry,
    params.sub_industry,
    params.funding_stage,
    params.scale,
    params.city,
    searchParams.get("sort"),
    searchParams.get("page"),
  ].some(Boolean);

  const changeFilter = (key: SearchParamKey, value: string) => {
    setSearchParams(withSearchParam(searchParams, key, value));
  };

  const clearFilters = () => {
    setSearchValue("");
    setSearchParams(new URLSearchParams());
  };

  // Kept only for the collection module's isolated compatibility tests. The production
  // route does not pass a registry, so the closed 100-company directory never collects.
  const legacyCollectionQuery = collectionRegistry
    && params.q
    && data?.total === 0
    && !error
    ? params.q
    : undefined;

  return (
    <main>
      <header className="app-header">
        <div className="content-width header-content">
          <a className="product-name" href="/list" aria-label="AI 公司榜首页">
            <span aria-hidden="true">企</span>
            AI 职业公司榜
          </a>
          <nav className="primary-nav" aria-label="主导航"><a href="/list">AI 榜单</a><a className="active" href="/companies">公司目录</a></nav>
        </div>
      </header>
      <div className="content-width workspace">
        <div className="workspace-heading">
          <div>
            <h1>AI 公司目录</h1>
            <p>在本期 AI 公司集合中，按名称、领域、规模与城市查找；校招与实习机会不影响榜单评分。</p>
          </div>
        </div>
        <Filters
          params={params}
          searchValue={searchValue}
          hasActiveFilters={hasActiveFilters}
          onSearchChange={setSearchValue}
          onFilterChange={changeFilter}
          onClear={clearFilters}
        />
        <CompanyResults
          data={data}
          error={error}
          loading={loading}
          sort={params.sort}
          hasActiveFilters={hasActiveFilters}
          onSortChange={(sort) => changeFilter("sort", sort)}
          onPageChange={(page) => setSearchParams(withPage(searchParams, page))}
          onClear={clearFilters}
          onRetry={() => setRetryCount((count) => count + 1)}
          emptyQueryStatus={legacyCollectionQuery ? <CollectionStatus query={legacyCollectionQuery} registry={collectionRegistry} /> : null}
        />
      </div>
    </main>
  );
}
