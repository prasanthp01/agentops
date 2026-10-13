# AgentOps release readiness

Verified locally on 13 October 2026. This assessment covers the shipped synthetic, self-hosted application. It is not a security certification or an endorsement for arbitrary customer workloads.

| Check | Result |
|---|---|
| Automated application, API, authorization and recovery regressions | 173 passed, no skipped tests |
| Locked Python dependency advisory audit | No known vulnerabilities reported |
| Built runtime image scan | No HIGH or CRITICAL vulnerabilities reported |
| Source secret and Docker configuration scan | No findings reported |
| Non-root container with resource limits | Built and started successfully |
| Local data and private material | Excluded from distribution; repository uses public noreply author identity |
| Documentation and licensing | Setup, architecture, API, security, operations and contribution instructions included |
| Hosted CI | Check the repository Actions run for the exact commit; local checks do not imply a hosted result |

## What works

Eligibility before ranking, transactional reservations, mock-provider retries and compatible fallback, usage settlement, scoped service identities, cancellation, durable recovery and configurable HTTPS origin enforcement. Coverage: 93%; golden routing evaluation: 12/12.

## Deployment boundary

The keyless quick start binds to loopback. Before internet exposure, configure your HTTPS origin, secure cookies, proxy request limits, appropriate storage permissions, monitored health and a verified restore path. Preserve synthetic-data and no-real-action defaults. Single-process or single-host limits in the runbook remain applicable.

Real model quality, external provider latency/billing, enterprise SSO, multi-region failover, long-running production load and independent penetration testing are not validated by this release. These are deployment-specific or optional extensions, not hidden working features. Public demos and historical evidence may run an earlier revision. Never infer an SLA from fixture timing or synthetic costs.

## Reproduce

Follow the README verification commands and CI workflow. Rerun dependency/container scans before deployment; an advisory-free scan describes its date and database, not future immunity. Keep credentials, test databases, raw logs and personal assets out of commits.
