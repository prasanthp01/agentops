# AgentOps end-to-end fixes — 13 October 2026

Historical application-flow audit for 0.1.2. The remaining image findings were subsequently resolved in 0.1.3; see [IMAGE_REMEDIATION.md](IMAGE_REMEDIATION.md).

Target: https://portfolio.gappazappa.in/projects/agentops. The initial live version was 0.1.0, source `eeda637fbe73`. This release is 0.1.2. Deployed at 2026-10-13T13:37:57Z and subsequently verified over public HTTPS.

## Reproduced problems and fixes

| Problem reproduced on the live app | Fix |
|---|---|
| Opening another tab replaced the session cookie; the original tab failed with `CSRF_REJECTED`. | Resume an unexpired session without rotating identity or extending expiry. Preserve pre-upgrade CSRF tokens, ownership isolation and origin checks. |
| Cancel while a new run's admission response was pending targeted the preceding run and could show its stale result. | Clear the prior run identifier before submission, disable Cancel until admission, and fence asynchronous callbacks by run generation. |
| HTML proxy/rate-limit errors appeared as JSON parser errors. | Parse responses defensively, show actionable errors and Retry-After delays, and bound request timeouts. |
| A transient startup failure permanently disabled submission until reload. | Add Retry connection and same-run reconnect, preserve edited input, and renew missing/expired sessions on submission. |
| Invalid numeric input inside collapsed advanced settings silently blocked submission without focusing the field. | Open the section synchronously before native validation focuses the invalid input. |
| A previously used browser combined cached 0.1.0 HTML with 0.1.1 JavaScript and failed initialization. | Revalidate public HTML/assets, version asset URLs, and create recovery controls when older cached markup lacks them. |

Also made the mobile drawer scrollable at short viewport heights, focused error messages, clarified cancelled-run wording, and prevented duplicate result-comparison entries.

## Verification before deployment

- **86 automated tests passed; 94% rounded line coverage.** Four new session regression tests cover two tabs, original expiry, expired-session isolation and compatibility with older CSRF tokens. A cache regression test verifies HTML/static revalidation, including HTTP 304. Existing cancellation, idempotency, policy, budget, durability and API authorization tests pass. One existing upstream Starlette/httpx deprecation warning remains.
- **12/12 golden routing cases pass.** No routing or provider changes.
- All nine browser scenarios pass; technical/evidence inspection, comparison and JSON export work. Export JSON was parsed and checked for consistent run IDs.
- Nine widths from 320 to 1920 px have no horizontal page overflow. Mobile drawer focus/Escape, short landscape scrolling, internal anchors and collapsed-input validation pass in cached Chromium 151.
- Nine browser resilience checks pass, including two simultaneous tabs, delayed admission, HTML 429/503/502, startup network failure, input preservation and reconnect without duplicate submission. Failure responses are intercepted in the isolated test browser; the live service is not deliberately disrupted.
- Ruff, formatting, JavaScript syntax, lockfile consistency and Git whitespace checks pass.

Receipts: `docs/evidence/live-e2e/`. Reproduction records end in `before.json`; local regression receipts start with `local-`. Browser regressions are reproducible with `scripts/browser-cache-compatibility.js`, `scripts/browser-resilience.js`, `scripts/browser-validation-export.js`, `scripts/browser-qa.js` and `scripts/browser-poll.js` through the Playwright CLI.

## Final public verification

Version **0.1.2**, source `f463eeb29037361226bcc0517584a9000200e654`, is live. Final image is `sha256:67044efa27ea1fff0c26e42720187e549fe66273f45b89c2dede54b9c2f13237`. The running source fingerprint matches the checkout.

All nine scenarios, nine resilience checks, older cached markup, the pre-upgrade browser session, numeric validation, parsed export, internal anchors, trace inspection, comparison, nine viewport widths, keyboard behavior, reduced motion and polling fallback passed on the final HTTPS deployment. Normal flows had no uncaught page errors or failed requests. Deliberately injected HTTP/network failures emitted expected browser network errors and recovered correctly.

Secure cookies, origin/CSRF checks, session ownership, body limits, disabled live mode, results and SSE passed public API verification. The homepage checksum and existing routes were preserved. Shared Nginx and Compose configurations were unchanged. The previous image and a consistent database backup are retained; an existing result and its settled usage survived the update.

Current receipts are `public-*.json`, `legacy-tab.json`, `previous-browser-final.json`, `staging.json`, `deployment.json`, `runtime-source.json` and `summary.json` under `docs/evidence/live-e2e/`. Intermediate 0.1.1 receipts are retained under `v0.1.1/`. Its first old-tab check missed second-tab initialization; that limitation is explicitly recorded and the final checks verify both tabs.

The exact final-image scan retained 44 HIGH package findings across eight existing CVEs, no CRITICAL and no HIGH/CRITICAL Python findings. No new advisories appeared relative to the first deployment using the same day's advisory database. The existing applicability assessment remains in the deployment report. These upstream findings are not claimed fixed.

## Scope

The application remains a keyless synthetic demonstration. These checks do not certify live-provider inference, cross-browser/device coverage, formal accessibility compliance, penetration resistance or sustained production capacity. No provider keys or paid inference were used. Existing unfixed base-image advisory findings remain documented in `docs/DEPLOYMENT.md`.
