import type { CompanySearchParams, CompanySort } from "../api/types";

export const PAGE_SIZE = 20;

export type SearchParamKey =
  | "q"
  | "industry"
  | "sub_industry"
  | "funding_stage"
  | "scale"
  | "city"
  | "sort";

const sorts = new Set<CompanySort>(["relevance", "name", "updated_at"]);

function positiveInteger(value: string | null) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : 1;
}
export function readCompanySearchParams(searchParams: URLSearchParams): CompanySearchParams {
  const q = searchParams.get("q")?.trim() || undefined;
  const requestedSort = searchParams.get("sort") as CompanySort | null;

  return {
    q,
    industry: searchParams.get("industry")?.trim() || undefined,
    sub_industry: searchParams.get("sub_industry")?.trim() || undefined,
    funding_stage: searchParams.get("funding_stage")?.trim() || undefined,
    scale: searchParams.get("scale")?.trim() || undefined,
    city: searchParams.get("city")?.trim() || undefined,
    page: positiveInteger(searchParams.get("page")),
    page_size: PAGE_SIZE,
    sort: requestedSort && sorts.has(requestedSort) ? requestedSort : q ? "relevance" : "updated_at",
  };
}

export function withSearchParam(
  current: URLSearchParams,
  key: SearchParamKey,
  value: string,
) {
  const next = new URLSearchParams(current);
  const normalized = value.trim();

  if (normalized) next.set(key, normalized);
  else next.delete(key);
  next.delete("page");
  return next;
}

export function withPage(current: URLSearchParams, page: number) {
  const next = new URLSearchParams(current);
  if (page <= 1) next.delete("page");
  else next.set("page", String(page));
  return next;
}
