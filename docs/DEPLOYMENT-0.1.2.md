# AgentOps deployment — 13 October 2026

**Live version 0.1.2:** https://portfolio.gappazappa.in/projects/agentops

Updated at 2026-10-13T13:37:57Z. Six issues found in live end-to-end testing are fixed. See [the audit report](LIVE_E2E_REPORT.md) for reproduction details and tests. This remains a keyless synthetic engineering demonstration; no real-provider inference or paid model calls are enabled.

## Current release

- Deployed source: `f463eeb29037361226bcc0517584a9000200e654`.
- Immutable image: `sha256:67044efa27ea1fff0c26e42720187e549fe66273f45b89c2dede54b9c2f13237`.
- Runtime source fingerprint: `c2aa5a1fba8287b2ade96aba6f100c3c2810e072fe5baa2873a129721703b8d1`; running container and local source match.
- Source transfer archive SHA-256: `ae2507dd45ad06b1053b287bc6f90fd10ae17d9ba1dff62dfbb161226eb8b178`.
- Python 3.12.15 / Debian 13 base remains pinned to `sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1`.
- Later documentation commits contain final receipts and do not change the running source. Initial 0.1.0 evidence and the intermediate 0.1.1 release are retained.

## Verification

| Check | Result |
|---|---|
| Automated regression | 86 passed; 94% rounded line coverage; one upstream Starlette/httpx deprecation warning |
| Golden routing corpus | 12/12 passed |
| Public browser | All nine scenarios; trace inspection, comparison, parsed JSON export and numeric validation |
| Responsive and keyboard | Nine widths, 320–1920 px; no horizontal overflow; mobile drawer and short landscape scrolling |
| Session and recovery | Existing/new tabs, legacy CSRF compatibility, delayed admission, friendly proxy errors, reconnect and startup retry |
| Deployment cache compatibility | Prior browser now initializes; intentionally old alert markup also works; HTML/assets revalidate and asset URLs are versioned |
| Public API | Secure cookie, CSRF/origin, ownership, body-size limits, live-mode refusal, result and SSE checks |
| Data | Online backup passed integrity check; existing result and settled attempt preserved through container recreation |
| Isolated final-image restore | Backup restored to disposable storage; prior result preserved and a new run succeeded |
| Isolation | Non-root 10001; read-only root; no capabilities; no-new-privileges; external connection blocked in staging |
| Existing portfolio | Homepage checksum, legacy route and method guards preserved; shared proxy/Compose files unchanged |

Receipts are under `docs/evidence/live-e2e/`. Fault injection occurred only inside isolated test browsers. No public outage, load test or provider failure was deliberately induced.

## Operation and recovery

Current source: `/opt/agentops/current` → `/opt/agentops/releases/f463eeb29037`. Compose project `agentops_prod`; service `agentops`; dedicated volume `agentops_prod_data`; internal network `agentops_edge`. The shared Nginx proxy connects to the existing network alias. No proxy configuration change was needed for this update.

```sh
cd /opt/agentops/current
docker compose --env-file operations/release.env -f operations/compose.production.yaml ps
curl --fail https://portfolio.gappazappa.in/api/projects/agentops/healthz
```

The pre-update database backup is `/data/pre-e2e-f463eeb.db` inside the private data volume. The backup is mode 0600 and passed integrity validation. No schema migration was introduced. Original session expiry is preserved; opening another tab no longer rotates identity. Session admission and request rate limits remain enforced.

At final verification the server had approximately **4.2 GiB free, 92% used**, and AgentOps used about **37 MiB / 384 MiB**. No unrelated storage was deleted. The older 98%-full figure belongs to the first deployment and is not the current reading.

To roll back this update, use the retained previous release `/opt/agentops/releases/4f327fc0bf34` with its saved `operations/release.env` and the same Compose project. Run `docker compose --env-file operations/release.env -f operations/compose.production.yaml up -d --no-deps --wait agentops` there, verify HTTPS health and a synthetic run, then atomically repoint `/opt/agentops/current`. Preserve the live data volume; do not run `down -v` or restore an old database over newer results. The previous image is `sha256:8455013272af1a77e77d48a115cb4eb032d09e5b1df1702a2e9e30dcbc74010b`. It predates the cache fix, so rollback reintroduces that known issue. A live rollback interruption was not performed.

Historical first-deployment setup and decommission instructions are in [DEPLOYMENT-0.1.0.md](DEPLOYMENT-0.1.0.md). Never replace current shared proxy files with stale whole-file backups. No off-host backup schedule or external alerting was added.

## Image scan disposition


Trivy 0.73.0 scanned the exact exported image using the advisory database fetched earlier on the same day. The HIGH/CRITICAL scan returned **44 package findings representing eight CVEs**, all HIGH; zero CRITICAL and zero HIGH/CRITICAL Python dependency findings. None had a Debian stable fix in the scanner database. The nonzero scanner result is retained; it is not reported as a clean scan.

The deployment assessment found no exposed application path to these issues under the verified runtime restrictions. That is an applicability assessment, not proof that the packages are vulnerability-free. Reassess after base-image updates, any shell/tool/archive feature, privilege change or private/live integration.

| Finding | Applicability evidence and primary source |
|---|---|
| CVE-2026-76642, CVE-2026-78408, CVE-2026-78409, CVE-2026-78410 | Local privileged mount/nsenter operations. AgentOps invokes neither; it has no effective capabilities, no host namespace/socket mounts and cannot gain privileges. Debian lists these as minor/no-DSA in stable: [mount helper](https://security-tracker.debian.org/tracker/CVE-2026-76642), [nsenter](https://security-tracker.debian.org/tracker/CVE-2026-78408), [subdirectory mount](https://security-tracker.debian.org/tracker/CVE-2026-78409), [bind mount](https://security-tracker.debian.org/tracker/CVE-2026-78410). |
| CVE-2026-54369 | Requires a privileged ACL caller operating on attacker-controlled paths. The service is unprivileged and does not call libacl or accept filesystem paths. [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-54369). |
| CVE-2025-69720 | Affects the infocmp command-line tool; the HTTP application never invokes it or accepts terminal descriptions. [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2025-69720). |
| CVE-2026-16742 | Affects systemd-homed, whose executable is absent from this image. [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-16742). |
| CVE-2026-9538 | Affects Perl Archive::Tar; the module is absent. No archive upload/extraction exists in the application. [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-9538). |

The owner still selects a source license and any public repository. Cross-project model-gateway adoption, real provider adapters and an independent review remain separate work.
