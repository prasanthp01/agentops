# AgentOps image remediation — 13 October 2026

## Scope and decision

Replace the vulnerable Debian runtime in AgentOps 0.1.2 with official Python 3.12.15 on supported Alpine 3.24. Apply updates from the stable Alpine repositories, retain hash-checked application dependency versions, require binary wheels, and remove unused `pip` and `ensurepip` installers after installation. This avoids installing build tools or mixing Debian stable with unstable repositories. No application routing or authorization behavior changes.

The current Debian tracker still lists the stable util-linux and ACL packages as vulnerable: [util-linux advisory](https://security-tracker.debian.org/tracker/CVE-2026-76642), [ACL advisory](https://security-tracker.debian.org/tracker/CVE-2026-54369). The replacement is the [official Python image source](https://github.com/docker-library/python/tree/2a3b794c223ab067d122719541cdd54a068732a5/3.12/alpine3.24); [Alpine's release policy](https://alpinelinux.org/releases/) lists 3.24 as supported. Its multi-platform index is pinned to `sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4`.

Remediation classification: a reversible runtime/base-distribution change, with native-library compatibility risk because Alpine uses musl. The user's explicit request authorizes remediation and redeployment. Release gates are an all-severity scan, regression tests inside the actual runtime image, isolated API/SSE and restore checks, a restore of the previous production database into disposable storage, and public HTTPS smoke tests. Preserve the prior immutable image and database; rollback changes the image, not stored usage records.

The Container Security skill's referenced `fixer-policy.md` was not present in the installed skills. No separate policy approval requirement was inferred. This is a scoped remediation with evidence for CIS Docker 4.1–4.4 and 4.6 and NIST SP 800-190 image/container risks, not a full CIS or Kubernetes certification.

## Baseline and candidate

Trivy 0.73.0 with the current advisory database found **172 package findings across 79 unique CVEs** in the previous image: 44 HIGH, 63 MEDIUM, 62 LOW, three UNKNOWN and zero CRITICAL. The prior report filtered to HIGH/CRITICAL; this run includes all severities. Six additional findings affected the bundled `pip`, which the running application does not need.

The candidate reports **zero known OS or Python package vulnerabilities at all severities**. No CVE ignore entries, severity exclusions or unfixed-vulnerability exclusions were used. The scanner detects Alpine 3.24.2 and Python dependencies. This is a timestamped scanner result, not a guarantee that unknown vulnerabilities do not exist.

The isolated candidate passed all 86 tests under the deployed resource and security restrictions. Pytest and its pure-Python test dependencies were mounted read-only from outside the image; production application modules and native extensions came from `/app` and `/usr/local`. The final runtime contains no test harness. The existing Starlette/httpx deprecation warning remains.

Evidence is under `docs/evidence/image-remediation/`. Final release identity, deployment and public verification receipts are recorded below. Original vulnerable-image reports and release archives are retained as historical evidence and rollback context.

## Reproduction

Build with the pinned Dockerfile, export the exact image using `docker image save`, then scan its archive:

```sh
trivy image --input image.tar.gz --scanners vuln --ignorefile /dev/null \
  --format json --output image-scan.json --exit-code 1
```

Do not add `--ignore-unfixed` or filter to HIGH/CRITICAL when assessing this release. Scan future rebuilt images again; stable package repositories and advisory databases evolve even when the base digest is pinned. Keep the verified output image digest as the deployed identity.

## Final deployment and verification

Version **0.1.3** is deployed at https://portfolio.gappazappa.in/projects/agentops.

- Source revision: `b46e48e123979718ac082f2e2f63f715ed80b814`.
- Immutable deployed image: `sha256:969b47493f7c777aaa8090c735e5f689bfb3ccccc342f5597187db14c052b89f`.
- Deployed at: `2026-10-13T13:57:14Z`; final verification: `2026-10-13T13:59:55.287519+00:00`.
- The exported image configuration matches the scanned configuration digest. The running application source fingerprint matches the tested checkout.
- **Zero known vulnerabilities at every severity** in the exact final image. Both Alpine OS and Python package analyzers ran. The current database was checked on this run; its advisory update is `2026-10-13T06:41:11.035783622Z`.
- **86/86 tests and 12/12 golden cases pass inside the exact final image**, with isolated temporary test tooling. One existing upstream deprecation warning remains.
- Startup, non-root identity, secure sessions, CSRF/origin, idempotency, SSE, private storage permissions and blocked outbound connections passed isolated staging.
- A backup from the old Debian deployment restored successfully into a disposable copy under Alpine; previous result/attempt data was preserved and a new simulation succeeded. SQLite is 3.53.4; no application schema migration was required.
- Public HTTPS checks passed: all nine scenarios, trace inspector, comparison, export, polling fallback, edited constraints, reduced motion, nine viewport widths and mobile keyboard handling. No uncaught browser page errors or unexpected failed requests occurred in these flows.
- The homepage checksum, legacy route and API guards remain unchanged. Shared proxy/Compose files were not modified. Existing result and usage settlement survived the container replacement.
- Non-root UID 10001, read-only filesystem, dropped capabilities, no-new-privileges, internal network and resource limits remain in force. Runtime memory was about 36 MiB; host storage approximately 3.7 GiB free / 93% used.

The restored backup is `/data/pre-image-b46e48e-r2.db` in the private data volume. Rollback is available through the retained 0.1.2 image and release directory; it would reintroduce the old image findings. The production database was not replaced or reset. Earlier artifacts remain unchanged.

### Focused container controls

| Control | Evidence | Outcome |
|---|---|---|
| CIS Docker 4.1: non-root identity | Runtime UID/GID 10001 | PASS |
| CIS Docker 4.2: trusted immutable base | Official Python index digest and source manifest | PASS |
| CIS Docker 4.3: remove unnecessary packages | No pip/ensurepip, no build compiler, binary-only dependency install | PASS |
| CIS Docker 4.4: patched/scanned image | Stable repository updates; all-severity final scan is zero | PASS |
| CIS Docker 4.6: health check | Deployed container healthy; public API health 0.1.3 | PASS |
| NIST SP 800-190: runtime isolation | Read-only root, no capabilities/new privileges, internal network, bounded resources | PASS |

This is a six-control review of the changed Docker image and existing Compose runtime. Kubernetes is not used. It is not a full CIS benchmark audit, penetration test or host vulnerability assessment.
