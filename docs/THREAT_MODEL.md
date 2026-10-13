# Focused threat model

| Threat | Boundary and control | Evidence | Residual limit |
|---|---|---|---|
| Other visitor reads/cancels a run | Opaque session, owner predicate on every read and cancellation | API ownership test across state/events/result/cancel | Browser/session theft remains an operator concern |
| Cross-site mutation | SameSite Strict, CSRF secret, Origin/fetch-site checks | CSRF/origin tests | HTTPS secure cookies must be enabled in deployment |
| Paid-model abuse | No outbound inference adapter in public or private queue; recognized live mode refused | Live/replay refusal tests | Real-model operation requires a new reviewed implementation |
| Budget race/duplicate accounting | Serial multi-account reservation, attempt uniqueness, amount checks | Concurrent reservation, settlement and dedup tests | Single-host SQLite only |
| Stale background worker writes | Lease expiry plus monotonic claim fence | Reclaim/cancel/recovery tests | No external effect exactly-once claim |
| Prompt injection / SSRF / command input | These fields are outside the accepted schema; no tool/network execution path | Hostile field tests, strict catalog enums | No claim of having tested a live LLM against injection |
| Secret leakage in errors | Generic validation/storage errors; no input echo or exception text logging | Canary error tests | Operator config/history/backup handling remains required |
| Resource exhaustion | Input bounds, rate buckets, concurrent runs/SSE, retention cap, bounded steps and age | Limit tests | Edge DDoS protection and target-host load validation pending |
| Malicious model output | Strict ProviderOutput schema, no markup rendering, caller-owned actions | Malformed output scenario | Real tool calls and models not connected |
| Supply chain | Locked hashes, pinned checkout action, dependency advisory and license inventory | Audit receipt and requirements lock | Container image digest/scan and remote CI not run |

The code review during this run was performed by the implementing agent. It is not represented as the fresh independent review requested for public release.
