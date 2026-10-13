# AgentOps deployment — 13 October 2026

**Live version 0.1.3:** https://portfolio.gappazappa.in/projects/agentops

The vulnerable Debian runtime has been replaced with a pinned official Python/Alpine runtime. Supported OS updates are applied, and unused pip/ensurepip installers are removed. Application dependency versions, authorization, routing and database schema are unchanged.

**Exact final image scan: zero known vulnerabilities at all severities**, down from 172 package findings. See [IMAGE_REMEDIATION.md](IMAGE_REMEDIATION.md) for the before/after scan, source rationale, runtime tests and limitations. The prior [application-flow fixes](LIVE_E2E_REPORT.md) remain included.

## Release identity

- Source: `b46e48e123979718ac082f2e2f63f715ed80b814`.
- Image: `sha256:969b47493f7c777aaa8090c735e5f689bfb3ccccc342f5597187db14c052b89f`.
- Base: `python:3.12.15-alpine3.24@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4`.
- Runtime source fingerprint: `b585bb4db3beccba43d7ae22f8c03f768b1faf28529570e6f6873fd5e18f2f6f`.
- Deployed: `2026-10-13T13:57:14Z`; verified: `2026-10-13T13:59:55.287519+00:00`.
- Current source directory: `/opt/agentops/current` → `/opt/agentops/releases/b46e48e12397`.

Subsequent documentation commits do not imply a different running image. Receipts are under `docs/evidence/image-remediation/`; older release evidence and archives are historical.

## Verification

The final image passed 86 regression tests and 12 golden routing cases inside its isolated runtime. An actual backup of the previous production database restored and processed a new simulation successfully. After cutover, all nine public browser scenarios, export/inspection/comparison, polling fallback, 320–1920 px layouts and keyboard checks passed. API verification confirmed secure cookies, CSRF/origin, run ownership, request bounds, disabled live mode and SSE. The existing homepage and routes are unchanged.

No CVE ignore entries, severity exclusions or unfixed-vulnerability exclusions were used. The scan covers known OS/Python package advisories in the recorded database; it is not proof of absence of unknown or future vulnerabilities. Host/kernel and unrelated container assessments are outside this change.

## Operations and rollback

Compose project `agentops_prod`; service `agentops`; dedicated volume `agentops_prod_data`; internal network `agentops_edge`. The existing proxy and Compose configuration are unchanged. Runtime remains UID 10001, read-only, capability-free, no-new-privileges, with 384 MiB / 0.5 CPU / 64 PID limits.

```sh
cd /opt/agentops/current
docker compose --env-file operations/release.env -f operations/compose.production.yaml ps
curl --fail https://portfolio.gappazappa.in/api/projects/agentops/healthz
```

The verified database backup is `/data/pre-image-b46e48e-r2.db` in the private data volume. Existing results and settled usage survived replacement. No database migration or reset was performed.

Previous release: `/opt/agentops/releases/f463eeb29037`; previous image: `sha256:67044efa27ea1fff0c26e42720187e549fe66273f45b89c2dede54b9c2f13237`. To roll back, run that release's Compose definition with its saved `operations/release.env`, using `up -d --no-deps --wait agentops`. Verify health and a synthetic run, then atomically repoint `/opt/agentops/current`. Keep the active data volume; never use `down -v` or restore an old backup over newer usage records. Rolling back reintroduces the old image vulnerabilities. A live rollback interruption was not performed.

At verification, memory use was approximately 36 MiB, with 3.7 GiB free / 93% used on the host. No unrelated data or images were pruned. No off-host backup schedule or external monitoring service was added. The application remains a keyless synthetic demonstration, with no real-provider inference or paid model calls enabled.

Historical details: [0.1.2 deployment](DEPLOYMENT-0.1.2.md), [0.1.0 deployment](DEPLOYMENT-0.1.0.md).
