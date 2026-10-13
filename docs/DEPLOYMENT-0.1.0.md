# AgentOps deployment — 13 October 2026

**Live:** https://portfolio.gappazappa.in/projects/agentops

Deployed at 11:26:43 UTC. This is the working **keyless, synthetic engineering demo**. Real-provider inference remains disabled; no provider keys or paid model calls were used. Public deployment does not imply a production capacity, independent security review, or live-model quality claim.

## Release identity

- Application: 0.1.0; deployed source: `eeda637fbe73f86af595191746083ddbc567fd17`.
- Immutable Docker image: `sha256:0aa39f454b4386a4a898cb2fc0e1e2a92f75acfb34d0b6b36c985c77cd8c81b9`.
- Official Python base index: `sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1`; Python 3.12.15, Debian 13.
- Source transfer archive SHA-256: `522bca0b61f0a81d84c92a3a986a637de0a32f719f86c941fc7f4204d72e72f8`.
- Later documentation commits contain the deployment receipts; they do not imply a different running image. The original local-build ZIP and its manifest are retained as historical artifacts.

## Verified

| Check | Result |
|---|---|
| Local regression suite after deployment preparation | 81 passed; 94% coverage; one existing upstream deprecation warning |
| Container build, startup and health | PASS on the deployment host |
| Runtime restrictions | UID 10001; read-only root; all capabilities dropped; no-new-privileges; 384 MiB / 0.5 CPU / 64 PID limits |
| Storage and network | Dedicated volume; directory 0700, DB 0600; internal Docker network; external connection attempt blocked |
| Restart persistence | Existing result and settled attempt survived; no duplicate settlement |
| Backup restoration | Consistent backup restored into disposable storage; prior result/attempt preserved and a new synthetic run succeeded |
| Public HTTPS API | Secure/HttpOnly/SameSite cookie, CSRF/origin, ownership, oversized input and live-mode refusal checked |
| Public browser | Nine scenario outcomes; export, comparison, trace inspection, polling fallback, meaningful input edit, reduced motion |
| Responsive layout | 320–1920 px, nine widths; no horizontal overflow; phone drawer keyboard checks; screenshots inspected |
| Existing portfolio | Homepage checksum unchanged across cutover; legacy ResolveAI route, read-only API restriction and unconnected-project response preserved |
| Proxy and rollback configuration | Both pass Nginx validation; active Compose config validates; HTTP redirects to HTTPS |

Actual public receipts are `docs/evidence/deployment-*.json`; browser screenshots are under `docs/evidence/deployment/`. Earlier local receipts retain their original timestamps and limitations. Remote CI, penetration testing, formal accessibility certification, sustained load and live-model evaluation were not performed.

## Server operation

The isolated service runs from `/opt/agentops/current` (a release symlink), with Compose project `agentops_prod`, container `agentops_prod-agentops-1`, volume `agentops_prod_data`, and internal network `agentops_edge`. Only AgentOps and the existing HTTPS proxy join that network. No host port is public; 8785 is bound to loopback.

```sh
cd /opt/agentops/current
docker compose --env-file operations/release.env -f operations/compose.production.yaml ps
curl --fail https://portfolio.gappazappa.in/api/projects/agentops/healthz
```

The existing Nginx portfolio vhost received only the AgentOps locations and rate-limit zones. The shared Compose definition gained the external `agentops_edge` network for the proxy, so recreation retains connectivity. Preserve these additions during future shared-stack deployments. A concurrent homepage deployment was detected before cutover; the patch was reconciled with its newer configuration rather than overwriting it. DNS, TLS certificates, the homepage handler and legacy ResolveAI content were preserved.

Nginx adds per-client limits; the application deliberately does not trust forwarded client headers. Its peer-based session admission therefore remains an aggregate ceiling of 20 new sessions per minute behind this proxy. Existing sessions last two hours; per-owner run limits still apply. This conservative configuration suits a small portfolio demo, not an asserted high-traffic service.

The final observed container memory was about 37 MiB. The filesystem remained **98% full, approximately 1.1 GiB free**. Deployment reclaimed 480.7 MB of unused Docker build cache; no images, containers, application data or volumes were pruned. Storage expansion or a separately scoped retention review remains operational follow-up. A Docker health check and restart policy are configured; no external alerting subscription or scheduled monitor was created.

## Recovery and rollback

The online backup/restore drill passed on the actual image. Its synthetic backup and receipt are in the dedicated data volume. Backups are not uploaded off-host or scheduled automatically; there is no off-host disaster-recovery guarantee. See `operations/runbook.md` for the non-overwriting backup utility.

The pre-cutover proxy/Compose copies are under `/opt/agentops/last-proxy-backup` (symlink to the timestamped backup). The live configuration hashes at cutover are in `deployment-cutover.json`.

For a rollback of this first deployment:

1. Compare current proxy and shared Compose hashes with that receipt. If another project changed either file, remove only the AgentOps locations, rate zones and network attachment from the current files. Do not restore a stale whole-file backup over newer work.
2. If hashes still match, restore the saved configuration **in place**, preserving the bind-mounted inode. Validate `docker compose config --quiet` and `docker exec gappazappa-nginx-1 nginx -t`, then gracefully reload Nginx.
3. Verify the homepage and legacy routes, then disconnect the proxy from `agentops_edge` and stop only the AgentOps service. Preserve the data volume and release image. Never use `down -v`.
4. Re-enable by validating the scoped route/network additions, starting the recorded immutable image, and checking HTTPS health and a synthetic run.

The backup configuration was validated in a disposable Nginx container. An actual public rollback/redeployment interruption was not performed. No database migration beyond the existing schema v1 was introduced.

## Image scan disposition

Trivy 0.73.0 scanned the exact exported image with a freshly downloaded advisory database. The HIGH/CRITICAL scan returned **44 package findings representing eight CVEs**, all HIGH; zero CRITICAL and zero HIGH/CRITICAL Python dependency findings. None had a Debian stable fix in the scanner database. The nonzero scanner result is retained; it is not reported as a clean scan.

The deployment assessment found no exposed application path to these issues under the verified runtime restrictions. That is an applicability assessment, not proof that the packages are vulnerability-free. Reassess after base-image updates, any shell/tool/archive feature, privilege change or private/live integration.

| Finding | Applicability evidence and primary source |
|---|---|
| CVE-2026-76642, CVE-2026-78408, CVE-2026-78409, CVE-2026-78410 | Local privileged mount/nsenter operations. AgentOps invokes neither; it has no effective capabilities, no host namespace/socket mounts and cannot gain privileges. Debian lists these as minor/no-DSA in stable: [mount helper](https://security-tracker.debian.org/tracker/CVE-2026-76642), [nsenter](https://security-tracker.debian.org/tracker/CVE-2026-78408), [subdirectory mount](https://security-tracker.debian.org/tracker/CVE-2026-78409), [bind mount](https://security-tracker.debian.org/tracker/CVE-2026-78410). |
| CVE-2026-54369 | Requires a privileged ACL caller operating on attacker-controlled paths. The service is unprivileged and does not call libacl or accept filesystem paths. [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-54369). |
| CVE-2025-69720 | Affects the infocmp command-line tool; the HTTP application never invokes it or accepts terminal descriptions. [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2025-69720). |
| CVE-2026-16742 | Affects systemd-homed, whose executable is absent from this image. [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-16742). |
| CVE-2026-9538 | Affects Perl Archive::Tar; the module is absent. No archive upload/extraction exists in the application. [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-9538). |

The owner still selects a source license and any public repository. Cross-project model-gateway adoption, real provider adapters and an independent review remain separate work.
