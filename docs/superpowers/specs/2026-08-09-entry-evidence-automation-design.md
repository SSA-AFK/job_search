# Entry Evidence Automation Design

## Goal

Establish auditable, scalable recruitment-entry evidence for the frozen Gate 1 manifest without treating an LLM as authority and without replacing prior discovery observations.

## Scope

The system reads only registered public government and association sources. It obtains candidate official websites, evaluates public recruitment-entry candidates, and records a new discovery round. It excludes job-list enumeration, logins, CAPTCHA handling, personal data, and any access-control bypass.

## Decision Pipeline

1. Deterministic extraction produces source URL, public page title, visible summary, and candidate website or recruitment URL.
2. Hard validation requires HTTPS, a registered source, robots approval, rate and request-budget compliance, and ownership evidence from an official website. It rejects credential-bearing URLs and login/CAPTCHA flows.
3. DashScope receives only the normalized public URL, title, visible summary, and anchor text. It returns structured confidence, evidence rationale, and risk labels. It never receives credentials, database strings, secrets, raw response bodies, or local paths.
4. A candidate is automatically accepted only if all hard checks pass and the model confidence meets the configured threshold. Low-confidence, conflicting group/subsidiary, cross-host, redirect, login, CAPTCHA, or inconsistent results become `review_required`.
5. Results accepted automatically are sampled independently by source and platform at 5%. One severe misattribution pauses automatic acceptance for that source/platform and routes later results to review.

## Persistence and Replay

Existing `not_found` observations remain immutable historical facts. A new explicit discovery round records the evidence-regeneration attempt and links to its predecessor; no manual deletion, status mutation, or overwrite is allowed. Reports show per-round and aggregate denominators separately.

## Safety and Success Criteria

- All requests stay within registered hosts, robots rules, one request per domain per second, and finite budgets.
- Every automated decision stores public evidence and a deterministic configuration/model fingerprint.
- Zero credentials, model prompts containing secrets, raw bodies, or database URLs appear in logs or artifacts.
- A 5% stratified audit exists for every source/platform with automatically accepted entries; any severe false attribution blocks that stratum.
- Discovery produces accepted entries only when ownership is evidenced. Stage 3B remains separately designed and approved before any job-list request.
