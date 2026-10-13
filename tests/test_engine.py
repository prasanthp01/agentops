import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from agentops.catalog import catalog, presets
from agentops.client import CachedPolicy, local_fallback
from agentops.contracts import SimulationInput
from agentops.engine import Circuit, filter_candidates


@pytest.mark.parametrize("preset", presets(), ids=lambda p: p["id"])
def test_golden_presets(run, preset):
    result, events = run(**preset["input"])
    expected = {
        "economical": "SUCCEEDED",
        "outage": "SUCCEEDED",
        "rate-limit": "SUCCEEDED",
        "region": "POLICY_BLOCKED",
        "budget": "BUDGET_EXHAUSTED",
        "malformed": "SUCCEEDED",
        "all-down": "AI_UNAVAILABLE",
        "agentops-down": "AI_UNAVAILABLE",
        "expired": "POLICY_BLOCKED",
    }
    assert result["output"]["outcome"] == expected[preset["id"]]
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert len({e["event_id"] for e in events}) == len(events)
    assert result["output"]["external_llm_spend_microusd"] == 0
    assert result["cost"]["kind"] == "SIMULATED"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"allowed_regions": ["US"]}, "REGION_FORBIDDEN"),
        ({"data_classification": "RESTRICTED"}, "DATA_CLASS_FORBIDDEN"),
        ({"required_capabilities": ["vision"]}, "CAPABILITY_MISMATCH"),
        ({"language": "fr"}, "LANGUAGE_UNSUPPORTED"),
        ({"input_tokens": 50000}, "CONTEXT_OVERFLOW"),
        ({"quality_floor": 95}, "QUALITY_BELOW_FLOOR"),
        ({"unavailable_models": ["cedar-eu"]}, "UNHEALTHY"),
        ({"latency_deadline_ms": 200}, "DEADLINE_INCOMPATIBLE"),
        ({"available_funds_microusd": 0}, "BUDGET_EXHAUSTED"),
    ],
)
def test_each_hard_filter(overrides, code):
    eligible, decisions = filter_candidates(SimulationInput(**overrides))
    assert code in decisions[0]["reasons"]
    assert "cedar-eu" not in [m.id for m in eligible]


def test_cheaper_ineligible_model_never_used_after_fallback(run):
    result, events = run(provider_failures={"cedar-eu": "503", "harbor-eu": "503", "vault-eu": "503"})
    assert result["status"] == "FAILED"
    assert all(
        e["technical"].get("model") != "swift-us" for e in events if e["step_type"] == "invoke_mock_provider"
    )


@pytest.mark.parametrize("strategy", ["lowest_cost", "priority", "fastest", "reliability"])
def test_strategy_has_deterministic_tiebreak(strategy):
    request = SimulationInput(strategy=strategy)
    a, _ = filter_candidates(request)
    b, _ = filter_candidates(request, list(reversed(catalog())))
    assert [m.id for m in a] == [m.id for m in b]
    assert a[0].id == ("harbor-eu" if strategy == "reliability" else "cedar-eu")


def test_meaningful_edits_change_selection(run):
    a, _ = run()
    b, _ = run(required_capabilities=["tools"])
    c, _ = run(data_classification="RESTRICTED")
    assert [r["output"]["selected_model"] for r in [a, b, c]] == ["cedar-eu", "harbor-eu", "vault-eu"]


def test_retry_after_and_usage(run):
    result, events = run(provider_failures={"cedar-eu": "429"}, retry_after_ms=777)
    waits = [e["technical"]["wait_ms"] for e in events if "wait_ms" in e["technical"]]
    assert waits == [777]
    assert result["output"]["modeled_latency_ms"] == 420 * 2 + 777
    assert len(result["output"]["usage"]) == 2
    assert result["output"]["usage"][0]["cost_microusd"] == 0


def test_retry_after_cannot_bypass_deadline(run):
    result, _ = run(provider_failures={"cedar-eu": "429"}, retry_after_ms=10000, max_fallbacks=0)
    assert result["status"] == "FAILED"
    assert result["output"]["attempt_count"] == 1


@pytest.mark.parametrize(
    "failure", ["503", "timeout", "invalid_json", "context_overflow", "restricted_region"]
)
def test_failure_falls_back_within_policy(run, failure):
    result, _ = run(provider_failures={"cedar-eu": failure})
    assert result["output"]["selected_model"] == "harbor-eu"
    assert result["output"]["model_fallback"]
    assert result["output"]["workflow_fallback"] is None


def test_fallback_ceiling_and_all_down(run):
    result, _ = run(provider_failures={"cedar-eu": "503"}, max_fallbacks=0)
    assert result["output"]["attempt_count"] == 2
    assert result["output"]["workflow_fallback"]["executed"] is False


def test_failed_model_output_cost_is_counted(run):
    result, _ = run(provider_failures={"cedar-eu": "invalid_json"})
    assert result["output"]["total_cost_microusd"] == 280 + 660
    assert result["output"]["usage"][0]["provider_outcome"] == "invalid_json"


def test_budget_bounds_cumulative_retry_cost(run):
    result, _ = run(provider_failures={"cedar-eu": "timeout"}, available_funds_microusd=300)
    assert result["output"]["total_cost_microusd"] <= 300
    assert result["output"]["outcome"] == "BUDGET_EXHAUSTED"


def test_deterministic_semantic_results(run):
    a, _ = run(provider_failures={"cedar-eu": "503"})
    b, _ = run(provider_failures={"cedar-eu": "503"})
    for obj in (a, b):
        for usage in obj["output"]["usage"]:
            usage.pop("usage_id")
            usage.pop("operation_id")
    assert a["output"] == b["output"]


def test_circuit_recovers_after_cooldown():
    circuit = Circuit()
    circuit.record("503", 10)
    circuit.record("timeout", 20)
    assert not circuit.allows(100)
    assert circuit.allows(1020)
    circuit.record("healthy", 1020)
    assert circuit.failures == 0 and circuit.opened_at_ms is None


def test_expired_or_unavailable_control_plane_does_not_call_provider(run):
    for overrides in ({"policy_version": "expired-policy"}, {"control_plane_available": False}):
        result, events = run(**overrides)
        assert result["output"]["attempt_count"] == 0
        assert not any(e["step_type"] == "invoke_mock_provider" for e in events)


def test_local_cache_never_spends_on_unprovable_quota():
    policy = CachedPolicy(
        policy_version="v1", application_id="resolveai", expires_at=datetime.now(UTC) + timedelta(hours=1)
    )
    assert local_fallback(policy, "resolveai")["billable_inference_allowed"] is False
    policy.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert local_fallback(policy, "resolveai")["outcome"] == "POLICY_BLOCKED"


def test_packaged_result_and_events_schema(run):
    result, events = run(provider_failures={"cedar-eu": "invalid_json"})
    root = Path("contracts/baseline")
    for filename, values in [
        ("simulation-result-v1.schema.json", [result]),
        ("simulation-event-v1.schema.json", events),
    ]:
        schema = json.loads((root / filename).read_text())
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
        for value in values:
            validator.validate(value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"input_tokens": -1},
        {"allowed_regions": []},
        {"quality_floor": 101},
        {"provider_failures": {"unknown": "503"}},
        {"api_key": "secret"},
        {"url": "http://169.254.169.254"},
        {"max_retries": 99},
    ],
)
def test_bounded_input(overrides):
    with pytest.raises(ValidationError):
        SimulationInput(**overrides)
