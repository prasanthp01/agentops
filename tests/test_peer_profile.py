"""The FlowOps review remains refused; profile interoperability cannot grant business authority."""

import copy
import secrets

import pytest
from fastapi.testclient import TestClient

from agentops import canonical_wire as wire
from agentops import peer_profiles
from agentops.canonical_app import PUBLIC_PREFIX as PREFIX
from agentops.canonical_app import create_app
from agentops.settings import Settings
from tests.test_canonical import SOURCE, config


def review(**changes):
    value = copy.deepcopy(peer_profiles.FLOWOPS_CONSTRAINTS)
    value.update(
        tenant_id="tenant-fixture",
        trace_id="synthetic-workflow",
        correlation_id="synthetic-correlation",
        operation_id="inference:synthetic-workflow",
        context_refs=["synthetic-workflow"],
        schema_version="1.0",
    )
    value.update(changes)
    return value


@pytest.fixture
def peer(tmp_path):
    token = secrets.token_urlsafe(32)
    with TestClient(
        create_app(Settings(db_path=tmp_path / "peer.db"), config(token), SOURCE, PREFIX)
    ) as client:
        client.headers["Authorization"] = "Bearer " + token
        yield client


def test_flowops_refusal_preserves_every_constraint_and_has_no_effect(peer):
    payload = review()
    original = copy.deepcopy(payload)
    wire.REGISTRY.validate("inference-request", payload)
    response = peer.post(PREFIX + "/inference", json=payload)
    assert response.status_code == 200, response.text
    decision = response.json()
    wire.REGISTRY.validate("route-decision", decision)
    assert payload == original
    assert decision["outcome"] == "POLICY_BLOCKED"
    assert decision["fallback_kind"] == "HUMAN_REVIEW"
    assert decision["attempts"] == [] and decision["selected_deployment"] is None
    assert decision["usage"] == dict(
        usage_id=decision["usage"]["usage_id"],
        kind="NONE",
        input_tokens=0,
        output_tokens=0,
        cache_tokens=0,
        provider_calls=0,
        cost={"kind": "NONE", "value": 0, "currency": "EUR", "pricing_version": "no-provider-usage"},
    )
    for field in ["operation_id", "trace_id", "correlation_id", "mode", "routing_policy_version"]:
        assert decision[field] == payload[field]
    assert response.headers["X-AgentOps-Capability-Profile"] == peer_profiles.FLOWOPS_REFUSAL
    assert "X-AgentOps-Run-ID" not in response.headers
    assert peer.post(PREFIX + "/inference", json=payload).json() == decision
    with peer.app.state.store.connection() as con:
        assert con.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM attempts").fetchone()[0] == 0
        assert con.execute("SELECT sum(used+reserved) FROM budgets").fetchone()[0] == 0


@pytest.mark.parametrize(
    "change",
    [
        {"risk_class": "LOW"},
        {"currency": "USD"},
        {"allowed_regions": ["eu"]},
        {"max_cost": 0.01},
        {"routing_policy_version": "policy-2026-10-v1"},
        {"context_refs": ["different-workflow"]},
        {"operation_id": "different-operation"},
        {"context_refs": []},
        {"environment": "production"},
        {"extensions": {"x_override": True}},
        {"task_type": "summarize"},
        {"max_output_tokens": 257},
    ],
)
def test_no_implicit_downgrade_or_expansion(peer, change):
    response = peer.post(PREFIX + "/inference", json=review(**change))
    assert response.status_code == 403, response.text
    wire.REGISTRY.validate("error", response.json())
    assert response.json()["code"] in ("FORBIDDEN", "POLICY_BLOCKED")
    with peer.app.state.store.connection() as con:
        assert con.execute("SELECT count(*) FROM runs").fetchone()[0] == 0


def test_authentication_precedes_profile_and_caps_are_explicit(peer):
    peer.headers.pop("Authorization")
    response = peer.post(PREFIX + "/inference", json=review())
    assert response.status_code == 401
    capabilities = peer.get(PREFIX + "/inference/capabilities").json()
    assert capabilities["signed_usage_delivery"]["status"] == "BLOCKED_NOT_IMPLEMENTED"
    assert capabilities["signed_usage_delivery"]["outbox"] is False
    refusal = capabilities["refusal_profiles"][0]
    assert refusal["outcome"] == "POLICY_BLOCKED" and refusal["business_action_completed"] is False
    assert refusal["context_dereferenced"] is False and refusal["usage_record_created"] is False


def test_refusals_are_rate_limited(tmp_path):
    token = secrets.token_urlsafe(32)
    with TestClient(
        create_app(
            Settings(db_path=tmp_path / "limited.db", runs_per_minute=1), config(token), SOURCE, PREFIX
        )
    ) as client:
        h = {"Authorization": "Bearer " + token}
        assert client.post(PREFIX + "/inference", json=review(), headers=h).status_code == 200
        result = client.post(PREFIX + "/inference", json=review(), headers=h)
        assert result.status_code == 429 and result.headers["Retry-After"] == "60"
        wire.REGISTRY.validate("error", result.json())
