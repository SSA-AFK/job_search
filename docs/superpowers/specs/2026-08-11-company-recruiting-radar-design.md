# Company Recruiting Radar — product and frontend design

**Date:** 2026-08-11
**Status:** Approved for planning
**Scope:** Present a company’s enriched profile and recruiting coverage to desktop users, from search results through company detail, using an isolated 20-company test database.

## Goal

Help a job seeker decide which companies to investigate without mistaking an uncollected or failed source for a company that is not hiring. The product must show concise, comparable signals in search results and complete, traceable facts in the company detail page.

The initial input is the 4,366-company Tianyancha export. The first execution imports the first 20 non-header company-name rows (worksheet rows 3–22) into a new, dedicated test database. Company names are a seed list, not proof of website, company profile, or recruiting status.

## Non-goals

- Do not circumvent logins, CAPTCHAs, robots restrictions, or recruitment-platform access controls.
- Do not claim complete coverage of every recruitment platform.
- Do not introduce job recommendations, candidate accounts, notifications, or automatic applications in this scope.
- Do not present imported Tianyancha data as a public source or export it to third parties without confirming the applicable license.

## User experience

### Search results

Each company row retains name, logo, industry, location and website, then adds a compact `RecruitingCoverageSummary`:

| Field | User-facing form | Rule |
| --- | --- | --- |
| Recruiting status | One semantic badge and a short label | Derived from the freshest usable entry snapshot, never inferred from a missing job list. |
| Active jobs | `N roles` when a complete current snapshot is available | Hidden when collection is incomplete or unavailable. |
| Last checked | Relative date with an exact date in accessible text | Uses the latest completed entry snapshot; otherwise says `Not checked`. |
| Profile completeness | e.g. `Profile: 6/8` | Counts present verified or pending profile facts; links to detail for the missing facts. |
| Website | Official website link or `Website pending` | A missing website is explicit, not blank. |

New filters are `Recruiting status`, `Last checked`, `Has active roles`, and `Profile completeness`; sorting adds `Most recently checked` and `Most active roles`. Filters must continue to work when counts are zero or unknown.

### Company detail

The page uses this order:

1. Identity header: name, aliases, official website, primary location and a recruiting-status badge.
2. Recruiting coverage panel: status explanation, current role count where trustworthy, last successful list check, entry URLs/platforms, and the next action or reason no reliable conclusion is available.
3. Active roles: title, city, employment type, pay when supplied, posting date, application links, source, and record update date.
4. Company profile: industry, sub-industry, funding, scale, headquarters, founded year, registration number and website. Each unavailable fact reads `Pending enrichment`, not a fabricated value.
5. Enrichment evidence: financing, filings, supplemental profile fields and source documents. Each displayed source retains provider, URL, coverage, fetch date, confidence and verification state.

The desktop UI must distinguish data states with text as well as color and preserve existing external-link and keyboard-focus behavior. Mobile-specific layouts are explicitly deferred.

## Recruiting status contract

The API exposes one of these values, plus its explanatory metadata. The UI must not recompute the state from partial client data.

| Status | Meaning | Required supporting data |
| --- | --- | --- |
| `active_roles` | A completed, fresh list snapshot found one or more active roles. | active-role count, completed-at, entry/source URL |
| `empty_confirmed` | A completed, fresh list snapshot confirmed zero roles. | completed-at, entry/source URL |
| `entry_discovery_pending` | No verified recruiting entry is available yet. | last discovery attempt when available |
| `collection_incomplete` | An entry exists but its latest attempt failed or was incomplete. | error category, attempted-at, entry/source URL when safe |
| `stale` | Previous evidence exists but has exceeded its freshness window. | last-successful-at, prior status |

`active_roles` and `empty_confirmed` are the only conclusive states. Error categories use user-safe language: `Robots restrictions`, `Login required`, `Verification required`, `Temporary source error`, or `Needs review`; raw provider errors remain internal.

## API and data changes

Extend the company list item and company detail response with a shared `recruiting_coverage` object:

```ts
type RecruitingCoverage = {
  status: "active_roles" | "empty_confirmed" | "entry_discovery_pending" | "collection_incomplete" | "stale";
  active_job_count: number | null;
  last_checked_at: string | null;
  last_successful_at: string | null;
  freshness: "fresh" | "stale" | "unknown";
  reason_code: string | null;
  entries: Array<{
    platform: string;
    url: string;
    status: string;
    last_checked_at: string | null;
  }>;
};
```

Add `profile_completeness` to list and detail responses:

```ts
type ProfileCompleteness = {
  present_fields: number;
  target_fields: number;
  missing_fields: string[];
  last_enriched_at: string | null;
};
```

The backend service owns freshness calculation and reason-code mapping. The existing collection snapshots, job entries, job sources, profile fields, filing records, funding events and source evidence remain the canonical records. No denormalized recruiting state may be persisted unless profiling proves a read-model is needed.

## Import and enrichment flow

1. The operator creates a new test database rather than using an existing development or production database. The import records its workbook name, worksheet and source row internally.
2. The importer reads worksheet rows 3–22, deduplicates exact normalized names, and creates the 20-company staging run. The source workbook remains local and is not uploaded to an external provider.
3. Identity resolution creates or matches a company; ambiguous matches stop for review.
4. Enrichment obtains profile facts and approved public recruiting entries, respecting robots and platform policies.
5. Each entry collection writes a complete, partial, empty or failed snapshot. Lifecycle rules close a role only after the existing complete-snapshot policy is satisfied.
6. The company read service builds recruiting coverage and profile completeness for the API.
7. The test scheduler runs only for the imported 20 companies. It prioritizes fresh active-role companies, then incomplete/retryable entries, then the long tail. It applies source and domain rate limits and queue backpressure.

## Failure handling

- No recruitment entry found: show `Recruiting entry pending`; do not show `No roles`.
- Failed/blocked collection: retain any prior evidence, mark the current status incomplete or stale, and show a human-safe reason.
- Profile enrichment unavailable: retain existing profile values and show missing fields as pending.
- Ambiguous company identity: exclude the company from automatic merging and expose no unverified external recruiting evidence.
- Empty role list: only show `No open roles confirmed` after a fresh, complete snapshot.

## Acceptance criteria

- A user can filter companies by each recruiting status and sort by freshness and active role count.
- Every row with a conclusive recruiting state identifies when it was checked; non-conclusive rows explain what is missing.
- The detail page shows all available baseline profile facts, job coverage, active roles and their sources without displaying empty fields as facts.
- An active role has at least title, city and application URL before it is counted as displayable.
- Source and verification details are visible in the detail page for every enriched fact that is not directly asserted by a first-party company website.
- API and UI tests cover all five recruiting states, missing profile fields, zero jobs, stale data, and desktop layout.
- The test database contains exactly the 20 imported companies before enrichment; no existing database records are read or modified.
- The test run reports an import count, identity-review count, recruiting-entry coverage, completed-list coverage, and active-role count for the 20-company cohort.
- Existing company search, detail and job-list behavior continues to pass.

## Delivery sequence

1. Create the isolated database configuration and deterministic 20-company Excel staging importer.
2. Define API contracts and service-level coverage/completeness builders with tests.
3. Add desktop list filters, badges and summary fields.
4. Add the detail coverage panel and profile/evidence presentation.
5. Dispatch and observe the 20-company cohort only after the presentation contracts are stable.
6. Verify backend test subsets, frontend unit tests, frontend production build and desktop end-to-end flow.
