"""Start a private real HTTP service; record sanitized, canonically validated receipts."""

import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agentops import canonical_wire as wire  # noqa: E402
from agentops.canonical_app import APP_VERSION, PUBLIC_PREFIX  # noqa: E402
from agentops.vendor.ecosystem_contracts import validate_stream  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    (ROOT / "work").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="contracts-http-", dir=ROOT / "work") as directory:
        directory = Path(directory)
        service_token = secrets.token_urlsafe(32)
        cfg = {
            "global_budget_microusd": 100000,
            "credentials": [
                {
                    "application_id": "flowops",
                    "environment": "test",
                    "tenant_id": "integration-fixture",
                    "audience": "agentops",
                    "token_sha256": hashlib.sha256(service_token.encode()).hexdigest(),
                    "scopes": ["inference:request", "usage:read"],
                    "budget_microusd": 100000,
                }
            ],
        }
        config_path = directory / "private.json"
        config_path.write_text(json.dumps(cfg))
        config_path.chmod(0o600)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(
            os.environ,
            AGENTOPS_DB=str(directory / "api.db"),
            AGENTOPS_SOURCE_REVISION=revision,
            AGENTOPS_CONTRACT_CONFIG=str(config_path),
            AGENTOPS_SECURE_COOKIES="false",
        )
        with (directory / "server.log").open("w") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "agentops.canonical_app:create_prefixed_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--no-access-log",
                    "--no-proxy-headers",
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15, trust_env=False) as client:
                    for _ in range(100):
                        try:
                            ready = client.get("/healthz")
                            if ready.status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        time.sleep(0.05)
                    else:
                        raise RuntimeError("Private service did not start")
                    receipts = {
                        "source_revision": revision,
                        "app_version": APP_VERSION,
                        "contract_version": "1.0.0",
                        "environment": "test",
                        "transport": "Real loopback HTTP / Uvicorn / durable SQLite worker",
                        "health": ready.json(),
                        "checked_at": wire.stamp(time.time()),
                        "scenarios": [],
                    }

                    def get(path, name, headers=None):
                        r = client.get(PUBLIC_PREFIX + path, headers=headers or {})
                        assert r.status_code == 200, (path, r.status_code, r.text)
                        wire.REGISTRY.validate(name, r.json())
                        return r.json()

                    meta = get("/simulation/schema", "simulation-schema")
                    assert wire.sha(meta["input_schema"]) == meta["input_schema_sha256"]
                    receipts["input_schema_sha256"] = meta["input_schema_sha256"]
                    ps = get("/simulation/presets", "simulation-presets")
                    token = client.post(PUBLIC_PREFIX + "/simulation/session").json()["token"]
                    headers = {"Authorization": "Bearer " + token}
                    for preset_id in ["economical", "outage", "economical"]:
                        p = next(x for x in ps["presets"] if x["preset_id"] == preset_id)
                        body = {
                            k: p[k]
                            for k in ["schema_version", "project_id", "mode", "preset_id", "input", "seed"]
                        }
                        body["client_correlation_id"] = "http-" + preset_id
                        started = client.post(PUBLIC_PREFIX + "/simulations", json=body, headers=headers)
                        assert started.status_code == 202, started.text
                        wire.REGISTRY.validate("simulation-run", started.json())
                        run = started.json()["run_id"]
                        for _ in range(200):
                            r = client.get(PUBLIC_PREFIX + f"/simulations/{run}/result", headers=headers)
                            if r.status_code == 200:
                                break
                            assert r.status_code == 409
                            wire.REGISTRY.validate("error", r.json())
                            time.sleep(0.01)
                        else:
                            raise AssertionError("Run did not finish")
                        result = r.json()
                        wire.REGISTRY.validate("simulation-result", result)
                        events = get(f"/simulations/{run}/events", "simulation-events", headers)
                        validate_stream(events["events"], run_id=run, project_id="agentops")
                        assert events["terminal"]
                        sse = client.get(
                            PUBLIC_PREFIX + f"/simulations/{run}/events?after=2",
                            headers={**headers, "Accept": "text/event-stream", "Last-Event-ID": "2"},
                        )
                        frames = [
                            json.loads(b.split("data: ", 1)[1])
                            for b in sse.text.split("\n\n")
                            if "event: step" in b
                        ]
                        validate_stream(frames, run_id=run, project_id="agentops", after=2)
                        assert frames == events["events"][2:] and "event: end" in sse.text
                        route = get(f"/simulations/{run}/route", "route-decision", headers)
                        receipts["scenarios"].append(
                            {
                                "input": body,
                                "run": started.json(),
                                "result": result,
                                "events": events,
                                "route": route,
                                "sse_replay": "PASS",
                            }
                        )
                    assert (
                        receipts["scenarios"][0]["result"]["output"]
                        == receipts["scenarios"][2]["result"]["output"]
                    )
                    assert (
                        receipts["scenarios"][0]["route"]["selected_deployment"]["deployment_id"]
                        == "cedar-eu-fixture"
                    )
                    assert (
                        receipts["scenarios"][1]["route"]["selected_deployment"]["deployment_id"]
                        == "harbor-eu-fixture"
                    )
                    private_headers = {"Authorization": "Bearer " + service_token}
                    request = {
                        "schema_version": "1.0",
                        "application_id": "flowops",
                        "environment": "test",
                        "tenant_id": "integration-fixture",
                        "task_type": "summarize",
                        "risk_class": "LOW",
                        "required_capabilities": ["TEXT", "STRUCTURED_OUTPUT"],
                        "data_classification": "INTERNAL",
                        "allowed_regions": ["eu"],
                        "latency_deadline_ms": 3000,
                        "max_input_tokens": 2000,
                        "max_output_tokens": 500,
                        "max_cost": 0.02,
                        "currency": "USD",
                        "routing_policy_version": "policy-2026-10-v1",
                        "correlation_id": "integration-service",
                        "trace_id": "integration-trace",
                        "operation_id": "integration-operation",
                        "mode": wire.MODE,
                        "context_refs": [],
                        "extensions": {},
                    }
                    r = client.post(PUBLIC_PREFIX + "/inference", json=request, headers=private_headers)
                    assert r.status_code == 200, r.text
                    wire.REGISTRY.validate("route-decision", r.json())
                    run = r.headers["X-AgentOps-Run-ID"]
                    repeat = client.post(PUBLIC_PREFIX + "/inference", json=request, headers=private_headers)
                    assert (
                        repeat.status_code == 200
                        and repeat.json() == r.json()
                        and repeat.headers["X-AgentOps-Run-ID"] == run
                    )
                    usage = get(f"/simulations/{run}/usage/1", "ai-usage-recorded", private_headers)
                    assert usage["usage"]["kind"] == "SIMULATED" and usage["usage"]["provider_calls"] == 0
                    receipts["private_service"] = {
                        "request": request,
                        "route": r.json(),
                        "usage": usage,
                        "duplicate_settlement": "PASS",
                    }
                    negative = []
                    for modified, status in [
                        (request | {"tenant_id": "foreign"}, 403),
                        (request | {"mode": "LIVE_MODEL_AUTHENTICATED"}, 503),
                    ]:
                        denied = client.post(
                            PUBLIC_PREFIX + "/inference", json=modified, headers=private_headers
                        )
                        assert denied.status_code == status
                        wire.REGISTRY.validate("error", denied.json())
                        negative.append(denied.json())
                    unauthorized = client.get(PUBLIC_PREFIX + f"/simulations/{run}/result", headers=headers)
                    assert unauthorized.status_code == 404
                    negative.append(unauthorized.json())
                    wire.REGISTRY.validate("error", unauthorized.json())
                    receipts["denials"] = negative
                    receipts["status"] = "PASS"
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(receipts, indent=2) + "\n")
                    print(
                        json.dumps(
                            {
                                "status": "PASS",
                                "source_revision": revision,
                                "synthetic_runs": 3,
                                "private_inference": "PASS",
                                "accounting": "SIMULATED_ONLY",
                                "sse_replay": "PASS",
                                "output": str(args.output),
                            }
                        )
                    )
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
