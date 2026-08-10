# Company Detail Enrichment Design

## Purpose

Enrich the existing list of companies directly in the current database so that each company detail page contains useful, traceable company, compliance, and recruitment information. The system may use public-web discovery and LLM extraction, but users must be able to distinguish verified facts from information awaiting verification.

## Displayed information

### Company overview

The detail page displays canonical name, aliases, official website, city, industry and sub-industry, company scale, funding stage, description, and logo.

- Official pages and official public registries are verified evidence.
- Public-web evidence discovered through Zhihu search is awaiting verification until its domain and content are validated as official.
- The model may summarize an already fetched source into a description or classify industry. Such values are always awaiting verification unless an authoritative source provides the exact value.

### Compliance and registration

The detail page displays ICP filing number, filing entity, related domain, filing status, and any available filing date. It also displays algorithm filing name, filing number, filing entity, publication batch or date, and status. Business registration remains an optional later extension.

ICP facts originate from the Ministry of Industry and Information Technology filing service. Algorithm filing facts originate from Cyberspace Administration of China publication lists. A name-only or domain-only match that cannot establish the company entity is stored as awaiting verification, not as confirmed registration.

### Recruitment

The detail page displays active-job count, title, job type, city, salary where published, description, publication date, application URL, and source.

The official careers site is preferred. Approved Feishu and Moka ATS URLs are fetched through the existing extractors. The model may categorize a fetched job or summarize its description, but it must never invent a job, application URL, filing number, or company entity.

## Provenance and verification metadata

Every collected item exposes a verification status (`verified` or `pending_verification`), source URL, fetched timestamp, and confidence score. Sources are shown in the existing “资料依据” section. Filing and job rows display the verification status directly.

The confidence score represents the extraction and identity-match confidence. It does not upgrade a pending item to verified.

## Collection pipeline

1. Treat the existing 100-company database list as the fixed batch target; do not clear or rebuild it.
2. Inventory each company field before collection: retain a current authoritative value, refresh a missing, stale, source-less, or model-derived value, and queue conflicts for review.
3. Use the Zhihu Global Search API only to discover candidate official domains, careers pages, ATS URLs, and public source pages.
4. Validate candidate URLs, official-domain ownership, and robots policy before fetching.
5. Fetch bounded official pages, official recruitment pages, and approved ATS pages.
6. Query authoritative ICP and algorithm-filing sources where an approved integration is available.
7. Pass only fetched source content to the LLM extractor. Require field-level source evidence and confidence.
8. Persist results directly to the existing company detail records with provenance and verification status.
9. Produce a batch report showing success, no-result, pending-verification, conflict, and failure reasons for each company.

## Source precedence and conflict handling

The precedence order is:

1. Official authoritative registries (MIIT/CAC).
2. Company-owned websites and official careers sites.
3. Approved ATS providers.
4. Public sources discovered by Zhihu search.
5. LLM-derived summaries and classifications.

Lower-precedence data never overwrites higher-precedence verified data. When equivalent-precedence sources conflict, retain the most recently fetched value, preserve the competing source in provenance, mark the field pending verification, and record the conflict in the batch report.

## Incremental refresh policy

The batch refreshes the current 100 companies field by field rather than recollecting blindly. A current, authoritative, traceable value is retained and receives an updated collection timestamp. A missing value, stale value, value without provenance, or model-derived value is queued for refresh. Conflicting values retain the authoritative record while the competing value remains pending verification.

Jobs are always re-fetched from official careers pages or approved ATS endpoints because their freshness is material to users. Existing jobs are updated or marked inactive through the established source lifecycle rules; the batch does not delete historical job records directly.

## Operational constraints

- Respect `robots.txt`, existing URL-safety controls, approved-host restrictions, concurrency caps, and rate limits.
- Never persist raw LLM output or secrets.
- Only actual fetched official recruitment records count as active jobs.
- Missing public information remains empty; it is never filled with an unsupported model guess.
- The batch must be idempotent so that re-running it refreshes evidence without duplicating filings, jobs, or sources.

## Acceptance criteria

- A company detail API response can expose provenance and verification state for every enriched item.
- The frontend visibly distinguishes verified from pending-verification filings and jobs.
- Zhihu results are discovery-only and cannot become verified without URL and content validation.
- LLM output is constrained to structured extraction or summarization of fetched evidence.
- The job collector invokes the existing official-site and ATS capability for approved URLs.
- A batch run creates a per-company report and safely continues after individual failures.
