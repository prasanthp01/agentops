# API and contract guide

Base path: `/api/projects/agentops`. The reverse proxy must preserve this prefix. OpenAPI is served at `/openapi.json` under that base; the generated snapshot is `contracts/openapi.json`.

## Public flow

1. `POST /session`: creates an HttpOnly, SameSite=Strict session cookie scoped to the API path, returning a CSRF token. Production HTTPS also requires `AGENTOPS_SECURE_COOKIES=true`.
2. `GET /simulation/schema`, `/simulation/presets`, `/catalog`: public bounded schemas and fictional provider information.
3. `POST /simulations`: send the session cookie, `X-CSRF-Token`, and optional `Idempotency-Key`; return HTTP 202 run state. Supply a v1.0 envelope with `project_id: agentops`, `mode: SANDBOX_DETERMINISTIC`, and the input object. Preset ID is descriptive metadata; submitted input is always executed.
4. `GET /simulations/{id}`: state and event count. `GET /simulations/{id}/events`: SSE; `Last-Event-ID` resumes from an integer sequence. Add `transport=poll&after=N` for ordered JSON events. Connections end after 30 seconds or a terminal state.
5. `GET /simulations/{id}/result`: HTTP 202 while processing, otherwise a v1.0 result. `POST /simulations/{id}/cancel`: CSRF-protected cancellation; already-terminal runs remain terminal.

All run reads and cancellations verify the current session/app owner. Unknown, other-owner or expired runs return the same 404. Run URLs are not authorization tokens. Read state does not grant permission to modify routing configuration. No admin mutation endpoints are exposed.

Example start body:

```json
{"schema_version":"1.0","project_id":"agentops","mode":"SANDBOX_DETERMINISTIC","preset_id":"outage","input":{"allowed_regions":["EU"],"provider_failures":{"cedar-eu":"503"}}}
```

Defaults for omitted nested input fields are defined in `contracts/SimulationInput.schema.json`. Unknown fields, empty allowed regions, unsupported modes and out-of-range values fail with 422. No arbitrary prompts, URLs, commands, credentials or tool definitions are accepted. Failure injection is restricted to the four synthetic model IDs and fixed scenario values.

## Authenticated service API

`POST /inferences` requires a bearer credential with `inference:write`. `GET /usage` requires `usage:read`; `GET /policy` requires `policy:read`. Body application/environment must match the credential's configured scope. Run ownership uses the app ID; service accounts within the same app share its policy budget. Admin configuration is an operator-owned file loaded at startup, not a public endpoint.

Configuration structure (replace the illustrative digest with SHA-256 of a newly generated high-entropy token; never use a human password):

```json
{"credentials":[{"application_id":"resolveai","token_sha256":"REPLACE_WITH_64_HEX_CHARACTERS","environments":["development"],"scopes":["inference:write","usage:read","policy:read"],"budget_microusd":100000}],"global_budget_microusd":200000}
```

This example intentionally does not validate until the placeholder is replaced. Keep configuration mode 0600; point `AGENTOPS_PRIVATE_CONFIG` to it. Rotation means replacing the digest and restarting. A removed credential immediately loses access after restart; a policy/app budget decrease below committed used/reserved amounts fails startup instead of erasing usage. Budgets are operator-defined lifetime allocations, not automatically replenished billing periods.

The private v1.1 request is described in `contracts/InferenceRequest.schema.json`. `operation_id` is the durable idempotency key. Each provider attempt gets a different usage ID. Prices are integer microdollars with explicit USD/version/date metadata. Usage export is limited to the latest 1,000 settled records and includes simulated budget totals; app/global totals survive run expiry. There is no reconciliation with provider invoices because no provider is billed.

`LIVE_MODEL_AUTHENTICATED` is a recognized request mode but returns 503 `LIVE_MODEL_NOT_CONFIGURED`; it never falls through to a mock success. The Python SDK exposes `submit` and `result`. `local_fallback` validates cache expiry/app identity and always denies billable inference without central budget authority.

## Errors and limits

Stable codes include `INVALID_INPUT`, `SESSION_REQUIRED`, `SESSION_EXPIRED`, `CSRF_REJECTED`, `ORIGIN_REJECTED`, `RUN_NOT_FOUND`, `IDEMPOTENCY_CONFLICT`, `RATE_LIMITED`, `CONCURRENCY_LIMIT`, `RETENTION_CAPACITY`, `STREAM_LIMIT`, `CALLER_SCOPE_MISMATCH`, `SCOPE_FORBIDDEN`, `LIVE_MODEL_NOT_CONFIGURED`, and `STORAGE_UNAVAILABLE`. Validation errors never echo submitted values or attacker-controlled property names. API 429 responses include Retry-After: 60. A routing refusal is a completed *failed run*, not HTTP authentication failure.

Request bodies are capped at 16 KiB even without Content-Length. Provider attempts are bounded by four candidates and at most three tries per candidate. Public ownership and service scopes are tested in `tests/test_api.py`; transaction and fence behavior is tested in `tests/test_store.py`.
