# AgentOps operations runbook

## Local execution

Run `uv sync --frozen`, then `uv run uvicorn agentops.app:create_app --factory --host 127.0.0.1 --port 8785 --no-access-log`. Use one ASGI process. Data lives under ignored `var/` by default. `AGENTOPS_DB` selects a local disk database. Do not mount it on NFS or start independent replicated hosts against it. `/healthz` checks SQLite access; the prefixed API health endpoint returns the running version and explicitly reports live inference disabled.

## Resource and security defaults

Two worker threads; 64 active jobs, four per owner, 2,000 retained runs; 60-second maximum job age; one-hour public and 30-day private run TTL; two-hour session TTL. Cleanup runs every minute. Mock invocations cannot reach provider networks. SQLite serializes writes; rising local latency is a reason to limit load or redesign storage, not add unbounded worker threads.

Keep DB directory and backups private. The DB file is mode 0600; SQLite sidecar and directory permissions also depend on the operator's umask. Use `umask 077` when starting the service. Secret configuration is outside the tracked source. No source of service credentials is present in the default build.

## Backup and restore drill

```sh
uv run python operations/backup.py var/agentops.db /secure/new-agentops-backup.db
```

The destination must not exist. Online backup produces a consistent DB, then verifies SQLite integrity. Encrypt it before storing off-host; encryption key management is not supplied by this project. Backups are not automatically uploaded.

To verify restoration, copy the backup to a new disposable local directory, start the service with `AGENTOPS_DB` pointing to the copy and a different loopback port, check integrity, inspect the known run/usage rows locally, and rerun a synthetic scenario. Never restore over the active database during a drill. Automated disposable restore verification is in `test_backup_restore_and_cleanup`; evidence receipts identify its result.

## Container staging

`docker compose config --quiet` validates configuration. `operations/deploy-local.sh` builds and starts only this named local Compose project. It never logs in to a server or edits a proxy. The image is non-root, filesystem read-only except its dedicated data volume and tmpfs, with dropped capabilities and resource/log limits. The Compose network is internal, and the published port is loopback only.

The deployed release pins official Python 3.12.15 on Alpine 3.24 and passed server build, health, volume ownership, database preservation, disposable restore and API/SSE checks. Its recorded all-severity image scan has zero known findings. Pip and ensurepip are absent at runtime; rebuild the image with updated hash-locked requirements to change dependencies. See `docs/IMAGE_REMEDIATION.md` for the current evidence. Repeat these checks for a new image; do not infer runtime success from Compose syntax validation. See `docs/DEPLOYMENT.md`.

## Shared-domain integration

Example deployment URL: `https://demo.example.com/projects/agentops`; API: `/api/projects/agentops/*`. The current Nginx deployment preserves full prefixes; see `operations/nginx-agentops.conf.fragment` and `docs/DEPLOYMENT_GUIDE.md`. `operations/Caddyfile.fragment` remains an alternative-host reference. Set secure cookies true behind HTTPS. The app does not trust X-Forwarded headers by default; the canonical Host is allowlisted. SSE buffering must be disabled. Add host/proxy connection-rate limits and tune forwarded client identity deliberately; do not trust arbitrary client-supplied X-Forwarded-For.

Session admission is limited by the connection peer. With an unconfigured reverse proxy, that may be the proxy itself, conservatively limiting all new sessions together. Staging must confirm desired fairness without making spoofable headers authoritative.

Inventory the existing proxy, services, storage, and available CPU/RAM/disk first. Back up configurations and prove DB restore. Test a temporary isolated route before authorized cutover. Never replace the existing homepage handler; AgentOps's local root redirect is not a portfolio homepage. Keep other applications and their data isolated.

## Rollback and failure drills

Record current image digest and configuration. Keep the previous image and a tested DB backup. Schema v1 has only additive initial tables; unknown schema versions refuse startup. Future migrations require new forward/backward review. Roll back by restoring the prior image/configuration against a compatible data copy and verifying in staging before changing production routes. Do not use `docker compose down -v`, destructive reseeding or a DB rollback that silently drops new usage records.

Drills implemented in tests: expired lease and stale worker, crash after settlement before result, duplicate request/usage, queued job restart, cancellation, expired run cleanup, shared budgets under concurrent reservations, storage backup/restore, provider outage and policy expiry. Real external provider uncertainty is outside the current queue.

## Troubleshooting

- 401: session expired or missing service credential; create a new session or rotate/configure the scoped token.
- 403: origin/CSRF or application/environment scope mismatch; do not disable the control to make a demo pass.
- 429: rate, concurrency, retained-run or stream limit. Wait for completion/TTL or reduce test load; do not delete evidence blindly.
- Routing `POLICY_BLOCKED`: inspect candidate rejection reasons and the versioned policy.
- `BUDGET_EXHAUSTED`: inspect reserved/used/cap values; adjust authorized allocation, never reset ledger totals.
- `RUN_TIMEOUT`: job exceeded its wall-time age bound; inspect local resource/storage contention.
- `STORAGE_UNAVAILABLE`: verify local disk space/permissions and SQLite integrity on a copy.
- Live request refused: intentional in v0.1.x; adding a provider key alone does not enable it.
