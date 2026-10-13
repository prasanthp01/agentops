# AgentOps Contracts v1 adoption

Current local adapter version `0.2.0-contracts.2`; canonical release `1.0.0`, wire `1.0`.
This adapter uses the existing AgentOps routing engine, worker fencing, transactional budget reservations and settlement ledger. It is a separate API entry point. The deployed 0.1.3 image, source archive, production configuration and previous scan receipts are unchanged.

The current profile handoff is [FLOWOPS_PEER_PROFILE.md](FLOWOPS_PEER_PROFILE.md), with tested source `f9b63cdc05be43d6953e27f02e117dda4079cdf9`. The base-adoption receipt in `docs/evidence/contracts-v1/` preserves the earlier tested implementation SHA. `contracts.lock.json` pins canonical release `b55d1a9dc5d2bab23817b984b9801ccff7304f04` and its tested source `02dacbc396189eb72809cfa228efc548d7e48925`. The importer verified all 49 source/schema hashes and all seven report hashes before copying the validator, 22 schemas, OpenAPI, license and 42 positive/negative fixtures. Vendored code is unmodified. No canonical or other product repository was edited.

## Private startup

From this repository, install locked dependencies with `uv sync --frozen`. Use a new, private SQLite path for integration. Do not point this experimental service at the production data volume or run two service factories against the same database. No database migration or destructive reset is needed; the adapter stores its metadata in the existing request JSON. Existing legacy rows are retained and hidden from the canonical interface.

For an edge that **strips** `/api/projects/agentops`, run:

```sh
AGENTOPS_SOURCE_REVISION="$(git rev-parse HEAD)" \
AGENTOPS_DB=work/contracts-private/agentops.db \
uv run uvicorn agentops.canonical_app:create_app --factory \
  --host 127.0.0.1 --port 8795 --no-access-log --no-proxy-headers
```

For a private test edge that preserves the prefix, use `agentops.canonical_app:create_prefixed_app` instead. This is the factory exercised by the real HTTP verifier. Both serve `/healthz`. Prefix-preserving mode also serves `/api/projects/agentops/healthz`. Readiness probes SQLite. The source revision must identify the build being tested; a packaged runtime without Git must set it explicitly.

The current production Nginx preserves the prefix and routes to the **legacy** factory. Integration must explicitly select the canonical upstream before advertising adoption publicly. The standalone legacy UI expects legacy envelopes. Do not redirect that UI's API calls to this factory until Portfolio uses the canonical client; its current UI remains functional on its existing upstream. No UI/static asset changes are included here.

For a future isolated container, build the existing pinned/remediated Dockerfile and override its command with `uvicorn agentops.canonical_app:create_app --factory --host 0.0.0.0 --port 8785 --no-access-log --no-proxy-headers`. Set `AGENTOPS_SOURCE_REVISION`, `AGENTOPS_DB`, and private config explicitly. Keep UID 10001, read-only root, dropped capabilities, no-new-privileges, internal networking and existing resource limits. A new image must be scanned and tested before deployment; the 0.1.3 zero-vulnerability receipt does not certify this unbuilt candidate. No new immutable image or production cutover is claimed.

## Session and simulation protocol

`POST /simulation/session` returns `{token, token_type: "Bearer", expires_in_seconds}`. Send the token as `Authorization: Bearer ...`, including with fetch-based SSE. Sandbox tokens have their own prefix, are stored hashed, expire after 7,200 seconds without sliding renewal, and cannot authorize private inference. Never place a token in a URL.

Optional `?transport=cookie` returns a CSRF token and sets `agentops_contract_session` (HttpOnly, SameSite=Strict, external path `/api/projects/agentops`). Set `AGENTOPS_SECURE_COOKIES=true` behind HTTPS. Send `X-CSRF-Token` on mutations. Origin and cross-site checks apply. Resuming a valid cookie preserves identity and original expiry. Direct internal root-path testing should use bearer tokens; the cookie path is deliberately the external project prefix.

Public metadata: `/simulation/schema`, `/simulation/presets`, `/openapi.json`, `/portfolio-evidence`. Run endpoints: `POST /simulations`, `GET /simulations/{run_id}`, `/result`, `/events`, and `POST /cancel`. `Idempotency-Key` is supported, 1–96 characters in the canonical ID alphabet. Identical requests return the same run; changed logical input conflicts. Source/version changes are part of the immutable request; reuse across an adapter upgrade can conflict and must be reconciled instead of blindly submitting a new operation.

Inputs are narrower than the generic envelope. The seed is top-level, integer 0–65,535. `provider_failures` is an array of `{model, outcome}` objects, at most four distinct synthetic models, rather than the legacy dictionary with hyphenated property names. The adapter validates the published input schema before Pydantic coercion. Unknown fields, nonempty extensions, live/replay mode and raw prompts are rejected.

Two complete inputs and a repeatable seed pair are in `examples/contracts-v1/`. At seed 42, `economical.json` selects Cedar EU; `outage.json` retries the simulated Cedar 503, rechecks policy and falls back to Harbor EU. Repeating the economical input produces equal semantic output; IDs and measured time differ. Nine existing presets are translated and tested, including all-down, expired policy, budget exhaustion and malformed output.

Limits: 16 KiB body; 16 global / 4 per-owner active runs; 60-second run deadline; public run retention 3,600 seconds; private operation retention 30 days; at most 1,024 steps (the bounded routing workflow emits substantially fewer). Event pages stop before 240 KiB and SSE closes before 250 KiB. Continue from the last received sequence. An idle active stream emits a heartbeat every 250 ms and closes after about 30 seconds for reauthentication/reconnection. Authentication is rechecked while streaming. SSE emits `step` and terminal `end`, never the legacy `done`. Both cursor forms must agree; malformed, negative and future cursors return typed 409. Pending results return typed 409. Owner-visible expired retained rows return 410; cleanup removes them, after which they return 404. Foreign run IDs always return 404, including expired records after a credential tenant/environment rebinding.

## Private inference, route and usage

Set `AGENTOPS_CONTRACT_CONFIG` to a mode-0600 JSON file with `global_budget_microusd` and `credentials`. Each credential has `application_id` (one canonical project), `environment`, `tenant_id`, fixed `audience: "agentops"`, `token_sha256`, `scopes` (`inference:request` and/or `usage:read`), and `budget_microusd`. Generate a random service token of at least 32 bytes; keep its plaintext in the private caller process and only its SHA-256 in this configuration. The verifier creates ephemeral credentials without publishing them.

One tenant/environment binding per application is supported because the existing budget account is application scoped. Duplicate application identities and duplicate token hashes are rejected. Payload application/environment/tenant must exactly match the authenticated binding. The endpoint fixes audience to AgentOps; it is not caller-selected. Reads and cancellations recheck the stored binding. Private config is separate from the legacy v1.1 credential file.

`POST /inference` accepts canonical `inference-request` and returns canonical `route-decision` after the durable worker settles (HTTP 200); `X-AgentOps-Run-ID` identifies the run. `operation_id` is the durable idempotency key. Duplicate calls return the same decision and do not settle again. On a waiting timeout, the operation remains durable; retry with the identical operation identity for reconciliation. A process outage never grants direct provider access.

Supported sandbox tasks: `summarize`, `classify`, `extract`. Regions: `eu`, `us`, `apac` (APAC has no eligible fixture). Capabilities: TEXT, STRUCTURED_OUTPUT, TOOLS, VISION. USD only; maximum cost is at most $1, input/output tokens 100,000/8,000, deadline 50–15,000 ms. Cost caps round **down** to integer microdollars. LOW risk uses quality floor 80, MEDIUM 90; HIGH is refused. Live/replay inference is unavailable. Context references and extensions are refused: no authorization to retrieve external content is inferred from a reference.

`GET /simulations/{run_id}/route` returns a canonical route decision; `GET /simulations/{run_id}/usage/{attempt}` returns one canonical `ai-usage-recorded` payload (1-based execution order), requiring a private `usage:read` grant. The route's attempts enumerate the usage records. Failed attempts, output validation failures and retries remain in the ledger. Business failures map to AI_UNAVAILABLE, POLICY_BLOCKED or BUDGET_EXCEEDED; original detailed outcomes remain in simulation output. Fallback to a different eligible model is MODEL; no selected model means HUMAN_REVIEW required of the caller, not executed here.

All usage is SIMULATED (or NONE when no attempt occurred), all provider-call counts are zero, and real spend is zero. The adapter rejects actual accounting records rather than relabeling them. Route attempt times are modeled; result duration is measured local execution time. Per-attempt usage and final costs reconcile. The original transactional run/application/global budget and lease/fencing protections remain in force.

## Verification and remaining gaps

Run `uv run pytest --cov=agentops --cov-fail-under=90` for canonical fixtures, real in-process endpoints, original regressions, security, concurrency and recovery. Run `uv run python scripts/verify_contracts_http.py --output work/contracts-http.json` for an actual loopback Uvicorn/HTTP/SQLite run through the canonical project prefix. It verifies two meaningfully different simulations, repeatability, events/poll/SSE replay, private inference, duplicate settlement, usage and denials. Fixtures and HTTP caller identities are synthetic; the backend execution is the real AgentOps implementation. The full FlowOps/ResolveAI callers are not exercised by this script. Private provider failure tests inject faults only into the existing mock adapter; no real provider is called.

IQ-02 is addressed for this **local canonical entry point**, subject to independent Integration/review verification. The currently deployed legacy endpoints remain incompatible until an authorized cutover. Cross-project signed usage delivery/outbox, actual provider adapters, invoice reconciliation, tenant-specific budget accounts, raw/context-bearing inference, and a real combined ecosystem edge are **not implemented or verified** by this change. `/events` ingestion and `/knowledge/query` are not advertised by AgentOps' OpenAPI. Usage payload conformance is not evidence of signed cross-service delivery.

The adoption evidence is separate from historical production evidence. No production deployment, shared proxy change, publication, migration, or paid call is authorized or performed in this handoff. Rollback for a future private canonical launch is stopping that private service and retaining its database; the existing live app remains on 0.1.3. Any eventual shared-origin cutover needs integration verification and separate human approval.

## Historical base-adoption revision and receipts

Implementation tested: `50bb507dafdd2c98d8d008c7307a9235439690dd`. Local verification: `2026-10-13T16:11:34.654938Z`.

The complete suite passed **152 tests**, including 42 canonical positive/negative fixtures, with **92.79%** statement coverage. All 12 golden cases passed. The actual private HTTP verifier passed normal/outage/repeated runs, event polling/SSE, private inference, usage, duplicate settlement and denied requests. The locked production Python dependency audit found zero known advisories. Static checks and source hygiene passed. One existing Starlette/httpx deprecation warning remains.

Receipts: `docs/evidence/contracts-v1/receipt.json`, `http.json`, `tests.txt`, `golden.json`, and `dependencies.json`. Root `portfolio-evidence.json` and the `/portfolio-evidence` response carry this canonical local test record. The prior production aggregate is preserved in `docs/evidence/contracts-v1/previous-production-evidence-0.1.3.json`; production itself is unchanged. Subsequent evidence/documentation commits do not change the tested implementation.

Current addition: the exact test-only FlowOps review profile returns a canonical POLICY_BLOCKED decision with NONE/zero EUR usage and HUMAN_REVIEW required of the caller. It creates no job or ledger record. Other constraints and existing routing remain unchanged. See `FLOWOPS_PEER_PROFILE.md` for the capability endpoint, exact matching, real peer-library evidence and the blocked signed-usage path. The current aggregate evidence represents that newer local verification; the historical base-adoption aggregate is retained.
