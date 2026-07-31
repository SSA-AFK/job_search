import type { CompanyListItem, CompanySearchParams, Page } from "./types";

export class ApiError extends Error {
  constructor(public readonly status: number) {
    super("API request failed");
    this.name = "ApiError";
  }
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
    if (!response.ok) throw new ApiError(response.status);
    return (await response.json()) as Page<CompanyListItem>;
  },
};
