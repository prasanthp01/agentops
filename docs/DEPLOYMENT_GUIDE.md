# Deployment requirements

This guide covers a bounded, single-operator synthetic service. It does not establish enterprise multi-tenancy or live model support.

1. Build the image from a reviewed commit with the committed lockfile. Record the image digest. Scan it for advisories; triage any remaining findings before exposing it.
2. Generate fresh secrets. Keep ignored configuration outside the image and repository. Set `AGENTOPS_PUBLIC_ORIGIN=https://router.example.com` and `AGENTOPS_SECURE_COOKIES=true`. Only the configured public hostname and local health-check hosts are accepted.
3. Use the production Compose template and a TLS reverse proxy you control. Preserve `/projects/agentops` and `/api/projects/agentops/` paths and SSE streaming. Do not replace an unrelated site's routing. Bind direct ports to loopback; allow only the intended ingress network.
4. Keep the database and worker on private networks. Apply migrations with the owner role; runtime uses its narrower identity. Set CPU, memory, process and log limits. Start health checks before accepting traffic.
5. Encrypt backups off host, preserve their keys separately, and run a disposable restore drill. Verify stored results and schema version, not merely that a backup file exists.
6. Confirm HTTPS cookie flags, origin/host rejection, run ownership and a successful synthetic run. Verify a refusal and a recovery scenario as well.
7. Monitor health, queue age, failures, disk use and retention. Configure alerts through your own operational system. Exercise image rollback before relying on it.

Run the provided recovery check only on an isolated environment:

```sh
uv run pytest tests/test_store.py -q
```

Review [operations](../operations/runbook.md) and [security](../SECURITY.md). Never reuse fixture credentials or CI databases for production. Optional private interfaces stay disabled until their credentials, network allowlists and budgets are explicitly configured.
