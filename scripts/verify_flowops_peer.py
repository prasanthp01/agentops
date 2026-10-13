"""Pinned, actual FlowOps PeerClient -> private AgentOps HTTP, with an unpersisted synthetic run."""

import argparse
import io
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agentops import canonical_wire as wire  # noqa: E402
from agentops.canonical_app import APP_VERSION, PUBLIC_PREFIX  # noqa: E402
from agentops.store import digest  # noqa: E402

FLOWOPS_SHA = "9e57bd7dfa6dc8eb7f11249b32e77fa70e438945"
CHILD = r"""
import json,sys
from datetime import UTC,datetime,timedelta
from flowops.db import Run
from flowops.interop import PeerClient,PeerGrant
from flowops.wire import REGISTRY
private=json.load(sys.stdin)
run=Run(id='flowops-peer-review-1',status='ACCEPTED',environment='test',
    deadline_at=datetime.now(UTC)+timedelta(seconds=60),input={'currency':'EUR'},
    context={'contracts_v1':{'tenant_id':'peer-synthetic','client_correlation_id':'peer-correlation'}})
grant=PeerGrant(run.id,'peer-synthetic',frozenset({'inference:request'}))
peer=PeerClient(private['url'],private['token'],allow_local_test=True)
original=peer._post
requests=[]
def capture(path,body,schema):
    requests.append(body)
    return original(path,body,schema)
peer._post=capture
result=peer.inference(run,grant)
REGISTRY.validate('route-decision',result)
assert result['outcome']=='POLICY_BLOCKED'
assert result['fallback_kind']=='HUMAN_REVIEW'
assert result['selected_deployment'] is None and result['attempts']==[]
assert result['usage']['kind']=='NONE'
assert result['usage']['provider_calls']==0
assert result['usage']['cost']['value']==0 and result['usage']['cost']['currency']=='EUR'
assert peer.inference(run,grant)==result
before=len(requests)
try:
    peer.inference(run,PeerGrant(run.id,'peer-synthetic',frozenset()))
except PermissionError:
    pass
else:
    raise AssertionError('Unscoped client call unexpectedly succeeded')
assert len(requests)==before
print(json.dumps({'request':requests[0],'response':result,'repeat':'PASS','unscoped_call_denied_locally':True,
    'source':'Actual pinned FlowOps PeerClient.inference; no transport mock; unpersisted synthetic Run'}))
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flowops-repo", type=Path, required=True)
    parser.add_argument("--flowops-python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    archive = subprocess.check_output(["git", "archive", FLOWOPS_SHA, "flowops"], cwd=args.flowops_repo)
    (ROOT / "work").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flowops-peer-", dir=ROOT / "work") as temp:
        temp = Path(temp)
        snapshot = temp / "caller"
        snapshot.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(snapshot, filter="data")
        token = secrets.token_urlsafe(32)
        config = {
            "global_budget_microusd": 100000,
            "credentials": [
                {
                    "application_id": "flowops",
                    "environment": "test",
                    "tenant_id": "peer-synthetic",
                    "audience": "agentops",
                    "token_sha256": digest(token),
                    "scopes": ["inference:request", "usage:read"],
                    "budget_microusd": 100000,
                }
            ],
        }
        config_path = temp / "private.json"
        config_path.write_text(json.dumps(config))
        config_path.chmod(0o600)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(
            os.environ,
            AGENTOPS_DB=str(temp / "server.db"),
            AGENTOPS_CONTRACT_CONFIG=str(config_path),
            AGENTOPS_SOURCE_REVISION=revision,
            AGENTOPS_SECURE_COOKIES="false",
            PYTHONDONTWRITEBYTECODE="1",
        )
        with (temp / "server.log").open("w") as log:
            server = subprocess.Popen(
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
                url = f"http://127.0.0.1:{port}"
                with httpx.Client(timeout=5, trust_env=False) as client:
                    for _ in range(100):
                        try:
                            response = client.get(url + "/healthz")
                            if response.status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        time.sleep(0.05)
                    else:
                        raise RuntimeError("Private AgentOps startup failed")
                    cap = client.get(url + PUBLIC_PREFIX + "/inference/capabilities")
                    assert cap.status_code == 200
                    health = response.json()
                child_env = dict(
                    os.environ,
                    PYTHONPATH=str(snapshot),
                    PYTHONDONTWRITEBYTECODE="1",
                    NO_PROXY="127.0.0.1,localhost",
                )
                # Supply the ephemeral token through stdin, never command arguments or receipts.
                child = subprocess.run(
                    # Preserve the venv symlink path so Python loads that environment's packages.
                    [str(args.flowops_python.absolute()), "-c", CHILD],
                    cwd=snapshot,
                    env=child_env,
                    input=json.dumps({"url": url + PUBLIC_PREFIX, "token": token}),
                    text=True,
                    capture_output=True,
                    timeout=20,
                )
                if child.returncode:
                    raise RuntimeError(
                        "Pinned FlowOps peer verification failed: "
                        + child.stderr.replace(str(ROOT), ".")[-2500:]
                    )
                result = json.loads(child.stdout)
                wire.REGISTRY.validate("inference-request", result["request"])
                wire.REGISTRY.validate("route-decision", result["response"])
                with sqlite3.connect(temp / "server.db") as db:
                    counts = {
                        t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                        for t in ["runs", "attempts", "events"]
                    }
                    spent = db.execute("SELECT sum(used+reserved) FROM budgets").fetchone()[0]
                assert counts == {"runs": 0, "attempts": 0, "events": 0} and spent == 0
                receipt = {
                    "status": "PASS_TYPED_REFUSAL",
                    "finding": "INT-FLOWOPS-PEER-PROFILE",
                    "agentops_source": revision,
                    "agentops_version": APP_VERSION,
                    "flowops_source": FLOWOPS_SHA,
                    "checked_at": wire.stamp(time.time()),
                    "health": health,
                    "capabilities": cap.json(),
                    "peer": result,
                    "agentops_database_counts": counts,
                    "used_plus_reserved_microusd": spent,
                    "production_modified": False,
                    "default_workflow_connected": False,
                    "signed_usage_delivery": "BLOCKED_NOT_IMPLEMENTED",
                    "limitations": [
                        "Peer-library interoperability only; no persisted FlowOps worker or business action.",
                        "Policy refusal is intentional. No supported model, dereferenced context, provider call or invoice.",
                    ],
                }
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(receipt, indent=2) + "\n")
                print(
                    json.dumps(
                        {
                            "status": receipt["status"],
                            "agentops_source": revision,
                            "flowops_source": FLOWOPS_SHA,
                            "provider_calls": 0,
                            "cost_eur": 0,
                            "jobs": 0,
                            "output": str(args.output),
                        }
                    )
                )
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == "__main__":
    main()
