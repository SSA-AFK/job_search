import type {
  CompanyDetail,
  CompanyListItem,
  CompanySearchParams,
  CollectionRequest,
  JobListItem,
  OverviewStats,
  Page,
} from "./types";

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly code?: string) {
    super("API request failed");
    this.name = "ApiError";
  }
}

async function apiError(response: Response) {
  let code: string | undefined;
  try {
    const body = await response.json() as { error?: { code?: string } };
    code = body.error?.code;
  } catch {
    // The HTTP status remains useful when an upstream error is not JSON.
  }
  return new ApiError(response.status, code);
}

function buildCompanyQuery(params: CompanySearchParams) {
  const query = new URLSearchParams();
  const optionalKeys = [
    "q",
    "industry",
    "sub_industry",
    "funding_stage",
    "scale",
    "city",
  ] as const;

  for (const key of optionalKeys) {
    const value = params[key]?.trim();
    if (value) query.set(key, value);
  }

  query.set("page", String(params.page));
  query.set("page_size", String(params.page_size));
  query.set("sort", params.sort);
  return query;
}

export const api = {
  async getCompanies(
    params: CompanySearchParams,
    signal?: AbortSignal,
  ): Promise<Page<CompanyListItem>> {
    const response = await fetch(`/api/v1/companies?${buildCompanyQuery(params)}`, { signal });
    if (!response.ok) throw await apiError(response);
    return (await response.json()) as Page<CompanyListItem>;
  },
  async getCompany(companyId: string, signal?: AbortSignal): Promise<CompanyDetail> {
    const response = await fetch(`/api/v1/companies/${encodeURIComponent(companyId)}`, { signal });
    if (!response.ok) throw await apiError(response);
    return (await response.json()) as CompanyDetail;
  },
  async getCompanyJobs(
    companyId: string,
    page: number,
    signal?: AbortSignal,
  ): Promise<Page<JobListItem>> {
    const query = new URLSearchParams({ page: String(page), page_size: "10" });
    const response = await fetch(
      `/api/v1/companies/${encodeURIComponent(companyId)}/jobs?${query}`,
      { signal },
    );
    if (!response.ok) throw await apiError(response);
    return (await response.json()) as Page<JobListItem>;
  },
  async createCollectionRequest(query: string, signal?: AbortSignal): Promise<CollectionRequest> {
    const response = await fetch("/api/v1/collection-requests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: query.trim() }),
      signal,
    });
    if (!response.ok) throw await apiError(response);
    return (await response.json()) as CollectionRequest;
  },
  async getCollectionRequest(requestId: string, signal?: AbortSignal): Promise<CollectionRequest> {
    const response = await fetch(`/api/v1/collection-requests/${encodeURIComponent(requestId)}`, { signal });
    if (!response.ok) throw await apiError(response);
    return (await response.json()) as CollectionRequest;
  },
  async getStatsOverview(signal?: AbortSignal): Promise<OverviewStats> {
    const response = await fetch("/api/v1/stats/overview", { signal });
    if (!response.ok) throw await apiError(response);
    return (await response.json()) as OverviewStats;
  },
};
