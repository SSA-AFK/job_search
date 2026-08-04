# Task 11 Report: Frontend Collection Polling

## Implementation

- Added `CollectionRequest` API types and typed POST/GET collection-request client methods with `AbortSignal` support.
- Added `collection/polling.ts` with the explicit `idle`, `submitting`, `queued`, `running`, `partial`, `succeeded`, `failed`, and `timed_out` reducer states; delays are exactly 2, 4, 8, then 10 seconds.
- Replaced the terminal-only collection status with an abortable polling component. It caches sessions by normalized query, reuses request IDs, stops after 120 seconds, offers a manual GET refresh, navigates successful requests to the returned company, and maps only known public error codes to UI copy.
- Preserved URL-driven search behavior and starts collection only for an empty unfiltered query result.
- Added an abort-aware StrictMode regression path: an effect replay retries only a canceled cached POST; ordinary rerenders do not submit again.
- Added focused component coverage and a desktop/mobile Playwright lifecycle flow with route interception, overflow assertions, and screenshots.

## RED Evidence

Initial required test command:

```powershell
cd frontend; npm test -- --run src/collection
```

Output: `CollectionStatus.test.tsx` had 9 tests with 7 failures and 2 passes. Expected lifecycle UI, navigation, timeout/manual-refresh control, and abort behavior were absent because the old component only modeled `loading`, `unavailable`, and `error`.

StrictMode regression RED command:

```powershell
cd frontend; npm test -- --run src/collection/CollectionStatus.test.tsx
```

Output: 10 tests with 1 failure. `recovers collection submission after a StrictMode effect replay cancels the first request` remained at `正在提交采集请求`, proving an aborted cached POST was reused instead of retried.

## GREEN Evidence

Focused component tests:

```powershell
cd frontend; npm test -- --run src/collection/CollectionStatus.test.tsx
```

Output: `1 passed`, `10 passed`.

Full Vitest:

```powershell
cd frontend; npm test -- --run
```

Output: `3 passed`, `38 passed`.

Production build:

```powershell
cd frontend; npm run build
```

Output: `tsc -b && vite build` completed successfully; `1601 modules transformed`.

Browser lifecycle test:

```powershell
cd frontend; npx playwright test tests/collection-flow.spec.ts
```

Output: `test-results/.last-run.json` reports `{"status":"passed","failedTests":[]}`. Desktop and mobile executed queued-to-running-to-success navigation, partial, public failed, and timeout states.

Diff hygiene:

```powershell
git diff --check
```

Output: exit 0 with no whitespace errors. Git emitted only existing LF-to-CRLF checkout warnings.

## Visual Checks

- Desktop screenshot: `frontend/test-results/collection-flow-collection-30f92-tes-without-layout-overflow-desktop/collection-status-desktop.png`
- Mobile screenshot: `frontend/test-results/collection-flow-collection-30f92-tes-without-layout-overflow-mobile/collection-status-mobile.png`
- Playwright asserted page `scrollWidth <= clientWidth` and collection-status bounds at both viewport profiles. Visual inspection found no overlap or text overflow; the mobile refresh button uses the existing full-width control treatment.

## Files Changed

- `frontend/src/api/client.ts`
- `frontend/src/api/types.ts`
- `frontend/src/collection/polling.ts`
- `frontend/src/collection/CollectionStatus.tsx`
- `frontend/src/collection/CollectionStatus.test.tsx`
- `frontend/src/search/SearchPage.tsx`
- `frontend/src/styles.css`
- `frontend/tests/collection-flow.spec.ts`

## Self-Review

- The reducer is explicit and covers every required state.
- Timers and asynchronous work live in effects, not rendering.
- Query change and unmount abort their active request/timer; stale results cannot update the current query.
- Error copy comes from a local allowlist rather than backend internal message content.
- Search-page session storage preserves one request per normalized query across result-loading remounts.
- Browser StrictMode cancellation is covered by a regression test.

## Concerns

None. The browser timeout flow advances only a test-local `Date.now()` offset after queueing; production polling uses real timers and the component fake-timer tests verify the exact 120-second boundary.
