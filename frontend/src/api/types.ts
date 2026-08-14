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
export type VerificationStatus = "verified" | "pending_verification";
export type RecruitingStatus = "active_roles" | "empty_confirmed" | "entry_discovery_pending" | "collection_incomplete" | "stale";
export type RecruitingCoverage = {
  status: RecruitingStatus;
  active_job_count: number | null;
  last_checked_at: string | null;
  last_successful_at: string | null;
  freshness: "fresh" | "stale" | "unknown";
  reason_code: string | null;
  primary_entry_url?: string | null;
  primary_entry_platform?: string | null;
};

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
  recruiting_coverage: RecruitingCoverage;
  ranking_status?: "ranked" | "observation";
  rank?: number | null;
  ranking_score?: number;
  company_stage?: "early" | "growth" | "mature";
  campus_job_count?: number;
  internship_job_count?: number;
  active_job_count?: number;
};

export type RankingComponents = {
  ai_core: number;
  market_validation: number;
  growth_momentum: number;
  industry_influence: number;
  reliability: number;
};

export type RankingMember = {
  company_id: string;
  company_name: string;
  rank: number | null;
  status: "ranked" | "observation";
  total_score: number;
  company_stage: "early" | "growth" | "mature";
  component_scores: RankingComponents;
  reason: string;
  missing_fields: string[];
  campus_job_count?: number;
  internship_job_count?: number;
  active_job_count?: number;
};

export type RankingList = {
  industry: "ai";
  rule_version: string;
  calculated_at: string;
  ranked_total: number;
  observation_total: number;
  page: number;
  page_size: number;
  total: number;
  items: RankingMember[];
};

export type Page<T> = {
  items: T[];
  page: number;
  page_size: number;
  total: number;
};

export type CollectionRequestStatus = "queued" | "running" | "partial" | "succeeded" | "failed";

export type CollectionRequest = {
  id: string;
  query: string;
  normalized_query: string;
  status: CollectionRequestStatus;
  company_id: string | null;
  error_code: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type FilingItem = {
  filing_type: "icp" | "algorithm" | "business_license";
  filing_number: string;
  filing_name: string;
  filing_authority: string | null;
  filing_date: string | null;
  filing_status: string | null;
  verification_status: VerificationStatus;
  detail_url: string | null;
};

export type CompanySourceSummary = {
  provider: string;
  url: string;
  title: string | null;
  covered_fields: string[];
  field_verification: Record<string, VerificationStatus>;
  confidence: string;
  published_at: string | null;
  fetched_at: string;
};

export type CompanyProfileFieldItem = {
  field_key: string;
  value: unknown;
  verification_status: VerificationStatus;
  collected_at: string;
};

export type FundingEventItem = {
  round_label: string;
  announced_at: string | null;
  amount: string | null;
  currency: string | null;
  investors: string[];
  verification_status: VerificationStatus;
};

export type CompanyDetail = CompanyListItem & {
  aliases: string[];
  headquarters: string | null;
  founded_year: number | null;
  established_at: string | null;
  province: string | null;
  district: string | null;
  company_type: string | null;
  registered_capital: string | null;
  paid_in_capital: string | null;
  industry_sector: string | null;
  industry_middle: string | null;
  insured_employee_count: number | null;
  employee_report_year: number | null;
  business_scope: string | null;
  latest_funding_round: string | null;
  filings: FilingItem[];
  sources: CompanySourceSummary[];
  profile_fields: CompanyProfileFieldItem[];
  funding_events: FundingEventItem[];
  job_count: number;
  ranking_rule_version?: string;
  ranking_calculated_at?: string;
  ranking_components?: RankingComponents;
  ranking_reason?: string;
  ranking_missing_fields?: string[];
  ranking_signals?: Array<{
    category: "ai_relevance" | "growth" | "intellectual_property" | "market_validation" | "material_risk";
    signal_key: "ai_business_scope" | "financing" | "ai_invention_patent" | "ai_software_copyright" | "winning_bid" | "active_qualification" | "material_risk";
    value: Record<string, unknown>;
    event_date: string | null;
  }>;
};

export type JobSourceItem = {
  provider: string;
  apply_url: string;
  verification_status: VerificationStatus;
};

export type JobListItem = {
  id: string;
  company_id: string;
  title: string;
  job_type:
    | "full_time"
    | "part_time"
    | "internship"
    | "temporary"
    | "campus"
    | "experienced"
    | "unknown";
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

export type NameCount = {
  name: string;
  count: number;
};

export type OverviewStats = {
  companies_total: number;
  companies_with_description: number;
  companies_with_website: number;
  jobs_total: number;
  jobs_with_city: number;
  crawl_runs_total: number;
  crawl_runs_by_status: NameCount[];
  jobs_by_city: NameCount[];
  jobs_by_type: NameCount[];
  companies_by_funding_stage: NameCount[];
  companies_by_scale: NameCount[];
};
