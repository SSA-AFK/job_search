export type CompanySort = "relevance" | "name" | "updated_at";

export type CompanySearchParams = {
  q?: string;
  industry?: string;
  sub_industry?: string;
  funding_stage?: string;
  scale?: string;
  city?: string;
  page: number;
  page_size: number;
  sort: CompanySort;
};
export type CompanyListItem = {
  id: string;
  canonical_name: string;
  industry: string | null;
  sub_industry: string | null;
  funding_stage: string;
  scale: string;
  city: string | null;
  logo_url: string | null;
  website: string | null;
  description: string | null;
  last_collected_at: string | null;
  created_at: string;
  updated_at: string;
};

export type Page<T> = {
  items: T[];
  page: number;
  page_size: number;
  total: number;
};
