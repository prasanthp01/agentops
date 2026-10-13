# Reproducing verification

Core checks use the locked Python environment:

```sh
uv sync --frozen
uv run ruff check agentops tests scripts operations
uv run ruff format --check agentops tests scripts operations
node --check agentops/static/app.js
uv run pytest --cov=agentops --cov-fail-under=90
uv run python -m scripts.evaluate
uv run python -m scripts.export_contracts
git diff --exit-code -- contracts
uv run pip-audit
uv run python -m scripts.check_source
docker compose config --quiet
```

`pip-audit` needs network access to its advisory source. Docker configuration validation does not require a running daemon; image build and runtime checks do. The existing lock was installed with uv 0.12.15 and tested on Python 3.12.14.

For browser verification, use an explicitly installed Playwright CLI and compatible browser. This run used cached CLI 0.1.20 with a configured existing Chromium 151 executable. No browser was downloaded. Start the local server from README, open an isolated CLI session at `/projects/agentops`, and run `playwright-cli -s=agentops run-code --filename=scripts/browser-qa.js`. The script uses real UI interactions and returns the receipt; it writes screenshots to `docs/evidence`. Then run `scripts/browser-poll.js` the same way to test the no-EventSource path and meaningful quality edit. The latter adds an initialization override to that isolated test session, so close it after testing.

Browser scripts do not contain credentials or profile paths. The environment-specific launcher configuration is intentionally excluded. Screenshots require human/model visual inspection in addition to the script's overflow checks. Targeted keyboard checks do not constitute a complete assistive-technology assessment.

`python -m scripts.write_evidence` assembles metadata from existing receipts; it does not run tests and must not be represented as a fresh verification pass. It preserves the separate runtime-source fingerprint and benchmark source revision. Rerun the relevant checks after executable changes.

## Public deployment evidence

The later HTTPS acceptance pass is recorded separately in `docs/DEPLOYMENT.md` and `docs/evidence/deployment-*.json`. The original local receipts remain historical evidence. `scripts.write_evidence` assembles a local-build snapshot and must not be used to overwrite deployment evidence or represent a new public verification.
