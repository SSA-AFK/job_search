export type CompanySort = "relevance" | "name" | "updated_at";
export type FundingStage =
  | "seed"
  | "angel"
  | "pre_a"
  | "series_a"
  | "series_b"
  | "series_c_plus"
  | "public"
  | "unfunded"
  | "unknown";
export type CompanyScale =
  | "one_to_49"
  | "50_to_199"
  | "200_to_499"
  | "500_plus"
  | "unknown";

export type CompanySearchParams = {
  q?: string;
  industry?: string;
  sub_industry?: string;
  funding_stage?: FundingStage;
  scale?: CompanyScale;
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
  funding_stage: FundingStage;
  scale: CompanyScale;
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

export type FilingItem = {
  filing_type: "icp" | "algorithm" | "business_license";
  filing_number: string;
  filing_name: string;
  filing_authority: string | null;
  filing_date: string | null;
  filing_status: string | null;
  detail_url: string | null;
};

export type CompanySourceSummary = {
  provider: string;
  url: string;
  title: string | null;
  covered_fields: string[];
  confidence: string;
  published_at: string | null;
  fetched_at: string;
};

export type CompanyDetail = CompanyListItem & {
  aliases: string[];
  filings: FilingItem[];
  sources: CompanySourceSummary[];
  job_count: number;
};

export type JobSourceItem = {
  provider: string;
  apply_url: string;
};

export type JobListItem = {
  id: string;
  company_id: string;
  title: string;
  job_type: "full_time" | "internship" | "campus" | "experienced" | "unknown";
  city: string;
  salary_min_monthly: number | null;
  salary_max_monthly: number | null;
  salary_months: number | null;
  description: string;
  posted_at: string | null;
  is_active: boolean;
  sources: JobSourceItem[];
  created_at: string;
  updated_at: string;
};
