# ADR 001 — Single-host keyless implementation and explicit contract evolution

Status: accepted for the authorized local build, 2026-10-13.

## Decisions

1. **SQLite WAL and Python workers.** The package targets a small VPS. A transactional local store avoids adding independent PostgreSQL/Redis services. It provides serial budget reservations, leases and recoverable jobs. This limits the supported topology to one host and one ASGI process with its worker pool; horizontal scaling needs a different concurrency design and new evidence.
2. **No real model calls in v0.1.0.** Default public behavior is keyless. The authenticated service gateway also supports only deterministic synthetic inference. Live mode, real pricing reconciliation, persistent provider-health control, and real provider adapters are deferred, not silently represented by fixtures.
3. **Versioned contract extension.** Original closed schemas are preserved verbatim under `contracts/baseline`. Public start/event/result envelopes conform to their v1.0 shapes. AgentOps-specific input is a strict nested schema. Provenance, limitations, warnings, request policy and version IDs reside under result `output`; optional error detail resides under event `technical`. Rich private inference is explicitly v1.1 and uses integer `max_cost_microusd`, not an unversioned change to v1.0's floating max_cost. Old v1.0 inference is not advertised as wire-compatible with v1.1.
4. **Conservative outage behavior.** The client reads integrity-controlled, operator-owned configuration with an explicit timezone-aware expiry. It is not cryptographically signed, and it never authorizes direct-provider spending while central quotas are unavailable. No claim of a verified offline spending authority is made.
5. **Buildless frontend.** Plain semantic HTML/CSS/JavaScript meets the interaction requirements while keeping installation and resource costs small. It uses no external font scripts, chart telemetry, or UI service. The local source trail remains visible for learning.
6. **Independent repository and route prefixes.** This checkout does not edit ResolveAI, the portfolio root, DNS, or shared proxy. A Caddy fragment and service package are provided for Session 06. Public repository publication, licensing and external deployment remain outside this run.
7. **Honest replay.** Replay-only mode is rejected; no user input is silently ignored. Provider fixture response shapes are intentionally small, validated synthetic outputs. Their content is not a generated incident report, business approval, or answer.

## Trade-offs

Synthetic costs reserve a full possible modeled call before each attempt. Known mock 429/503/overflow/region failures consume zero synthetic tokens; invalid output consumes the full fixture input/output count; timeouts consume the fixture input count. This is a documented fixture policy, not an assertion about any real provider's billing. A live adapter would need independent usage uncertainty, held reservations, reconciliation and price evidence.

A successful local test suite does not establish live-model quality, production readiness, security certification, or independent review. Container image digest resolution and container runtime verification remain release tasks when a daemon is available.
