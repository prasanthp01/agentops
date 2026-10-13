"""Contracts v1 projections of AgentOps domain records; no routing decisions here."""

import hashlib
import json
from datetime import UTC, datetime

from pydantic import Field

from .catalog import FIXTURES_VERSION, POLICY_VERSION, catalog, presets
from .contracts import Failure, SimulationInput, StrictModel
from .vendor.ecosystem_contracts import SchemaRegistry

REGISTRY = SchemaRegistry()
MODE = "SANDBOX_DETERMINISTIC"
CAPABILITIES = {"text": "TEXT", "json": "STRUCTURED_OUTPUT", "tools": "TOOLS", "vision": "VISION"}
REGIONS = {"EU": "eu", "US": "us", "APAC": "apac"}
ERROR_STATUS = {
    "UNAUTHENTICATED": 401,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "RUN_EXPIRED": 410,
    "PAYLOAD_TOO_LARGE": 413,
    "VALIDATION_ERROR": 422,
    "RATE_LIMITED": 429,
    "AI_UNAVAILABLE": 503,
    "POLICY_BLOCKED": 403,
    "BUDGET_EXCEEDED": 429,
    "SERVICE_UNAVAILABLE": 503,
    "INVALID_CURSOR": 409,
}


def checked(name, value):
    try:
        REGISTRY.validate(name, value)
    except ValueError:
        # A broken server projection is an availability fault, not a client validation error.
        raise RuntimeError("Canonical response failed validation") from None
    return value


def stamp(ts):
    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


def sha(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def error(code, correlation="agentops-error"):
    result = {
        "schema_version": "1.0",
        "code": code,
        "message": code.replace("_", " ").capitalize() + ".",
        "retryable": code in ("RATE_LIMITED", "SERVICE_UNAVAILABLE", "AI_UNAVAILABLE"),
        "correlation_id": correlation,
        "http_status": ERROR_STATUS[code],
    }
    if code == "RATE_LIMITED":
        result["retry_after_seconds"] = 60
    return checked("error", result)


class FailureFixture(StrictModel):
    model: str = Field(pattern=r"^(cedar-eu|harbor-eu|swift-us|vault-eu)$")
    outcome: Failure


def input_schema():
    value = SimulationInput.model_json_schema()
    value["title"] = "AgentOps canonical synthetic input v1"
    value["properties"].pop("seed")
    fixture = FailureFixture.model_json_schema()
    value["properties"]["provider_failures"] = {
        "type": "array",
        "maxItems": 4,
        "items": fixture,
        "default": [],
    }
    return value


def to_domain_input(value, seed):
    # Validate JSON types before Pydantic's coercion, and forbid unknown input/fixture fields.
    from jsonschema import Draft202012Validator

    from .vendor.ecosystem_contracts import ContractError

    if next(Draft202012Validator(input_schema()).iter_errors(value), None) or seed > 65535:
        raise ContractError("Invalid AgentOps input or seed")
    value = dict(value)
    failures = value.pop("provider_failures", [])
    if len({x["model"] for x in failures}) != len(failures):
        raise ContractError("Duplicate failure fixture")
    value["provider_failures"] = {x["model"]: x["outcome"] for x in failures}
    return SimulationInput(**value, seed=seed).model_dump()


def schema(settings):
    inp = input_schema()
    return checked(
        "simulation-schema",
        {
            "schema_version": "1.0",
            "project_id": "agentops",
            "input_schema_id": "agentops-input-v1",
            "input_schema_sha256": sha(inp),
            "input_schema": inp,
            "supported_modes": [MODE],
            "limits": {
                "max_body_bytes": 16384,
                "max_steps": 1024,
                "max_run_seconds": settings.max_run_seconds,
                "retention_seconds": settings.run_ttl_seconds,
                "max_concurrent_per_session": settings.max_per_owner,
                "max_concurrent_global": settings.max_active,
            },
        },
    )


def preset_list():
    items = []
    for p in presets():
        inp = dict(p["input"])
        seed = inp.pop("seed")
        inp["provider_failures"] = [{"model": k, "outcome": v} for k, v in inp["provider_failures"].items()]
        items.append(
            checked(
                "simulation-preset",
                {
                    "schema_version": "1.0",
                    "project_id": "agentops",
                    "preset_id": p["id"],
                    "title": p["title"],
                    "description": p["description"],
                    "input": inp,
                    "seed": seed,
                    "mode": MODE,
                    "fixtures_version": FIXTURES_VERSION,
                },
            )
        )
    return checked(
        "simulation-presets", {"schema_version": "1.0", "project_id": "agentops", "presets": items}
    )


def metadata(row):
    return json.loads(row["request"])["contract_v1"]


def usage(identifier, rows):
    if any(
        r.get("kind") != "SIMULATED"
        or r.get("external_llm_spend_microusd") != 0
        or r.get("currency") != "USD"
        for r in rows
    ):
        raise RuntimeError("Synthetic accounting cannot project actual provider spend")
    kind = "SIMULATED" if rows else "NONE"
    cost = {
        "kind": kind,
        "value": sum(r["cost_microusd"] for r in rows) / 1000000,
        "currency": "USD",
        "pricing_version": "synthetic-usd-v1",
    }
    return {
        "usage_id": identifier,
        "kind": kind,
        "input_tokens": sum(r["input_tokens"] for r in rows),
        "output_tokens": sum(r["output_tokens"] for r in rows),
        "cache_tokens": 0,
        "provider_calls": 0,
        "cost": cost,
    }


def run(row, count):
    terminal = row["status"] not in ("ACCEPTED", "RUNNING")
    return checked(
        "simulation-run",
        {
            "schema_version": "1.0",
            "project_id": "agentops",
            "run_id": row["id"],
            "mode": MODE,
            "status": row["status"],
            "created_at": stamp(row["created"]),
            "completed_at": stamp(row["completed"]) if terminal else None,
            "expires_at": stamp(row["expires"]),
            "event_count": count,
            "last_sequence": count,
            "result_ref": f"/simulations/{row['id']}/result" if terminal else None,
            "provenance": metadata(row)["provenance"],
        },
    )


def evidence(row, raw, suffix):
    return {
        "evidence_id": f"evidence-{suffix.replace('/', '-')}",
        "origin": "FIXTURE",
        "reference": f"simulations/{row['id']}/{suffix}",
        "sha256": sha(raw),
    }


def event(row, raw):
    action = raw["step_type"]
    category = (
        "SUMMARY"
        if action == "result"
        else "VALIDATION"
        if action.startswith("validate")
        else "EXECUTION"
        if action in ("invoke_mock_provider", "usage_record", "retry_or_fallback")
        else "POLICY"
    )
    data = raw["technical"]
    rows = [data] if action == "usage_record" else []
    return checked(
        "simulation-event",
        {
            **{
                k: raw[k]
                for k in (
                    "schema_version",
                    "run_id",
                    "project_id",
                    "event_id",
                    "sequence",
                    "step_id",
                    "component",
                    "status",
                    "mode",
                    "title",
                    "overview",
                )
            },
            "occurred_at": raw["occurred_at"].replace("+00:00", "Z"),
            "step_type": category,
            "technical": {
                "action": action,
                "input_schema_version": "agentops-input-v1",
                "output_schema_version": "agentops-output-v1",
                "input_ref": row["id"],
                "output_ref": raw["event_id"],
                "decisions": [raw["overview"][:240]],
                "evidence": [evidence(row, data, f"events/{raw['sequence']}")],
                "adapter_kind": "DETERMINISTIC",
                "attempt": data.get("attempt", 1),
                "fallback_reason": str(data["reason"])[:240] if "reason" in data else None,
            },
            "duration_ms": raw["duration_ms"],
            "trace_id": metadata(row)["trace_id"],
            "usage": usage(f"usage-{raw['event_id']}", rows),
            "provenance": metadata(row)["provenance"],
        },
    )


def result(row, rows):
    raw = json.loads(row["result"]) if row["result"] else {}
    domain = raw.get("output", {})
    prov = metadata(row)["provenance"]
    outcome = domain.get("outcome", row["status"])
    accounting = usage(f"usage-{row['id']}", rows)
    output = {
        k: domain[k]
        for k in (
            "outcome",
            "reason",
            "selected_model",
            "provider_output",
            "candidate_decisions",
            "model_fallback",
            "fallback_hops",
            "workflow_fallback",
            "attempt_count",
            "modeled_latency_ms",
            "total_cost_microusd",
            "external_llm_spend_microusd",
        )
        if k in domain
    }
    output.setdefault("outcome", outcome)
    output["settled_attempts"] = len(rows)
    output["external_llm_spend_microusd"] = 0
    code = {
        "BUDGET_EXHAUSTED": "BUDGET_EXCEEDED",
        "POLICY_BLOCKED": "POLICY_BLOCKED",
        "AI_UNAVAILABLE": "AI_UNAVAILABLE",
        "CANCELLED": "CONFLICT",
    }.get(outcome, "SERVICE_UNAVAILABLE")
    return checked(
        "simulation-result",
        {
            "schema_version": "1.0",
            "project_id": "agentops",
            "run_id": row["id"],
            "mode": MODE,
            "status": row["status"],
            "app_version": prov["app_version"],
            "fixtures_version": prov["fixtures_version"],
            "provenance": prov,
            "output_schema_version": "agentops-output-v1",
            "output": output,
            "safety_status": "SAFE" if row["status"] == "SUCCEEDED" else "BLOCKED",
            "evidence": [evidence(row, raw, "domain-result")],
            "errors": [] if row["status"] == "SUCCEEDED" else [error(code, metadata(row)["correlation_id"])],
            "warnings": [],
            "limitations": [
                "Synthetic models only; no real provider calls or billed usage.",
                "Attempt durations are modeled. Workflow fallback is required of the caller, not executed here.",
            ],
            "duration_ms": min(
                120000, raw.get("duration_ms", max(0, (row["completed"] - row["created"]) * 1000))
            ),
            "usage": accounting,
            "cost": accounting["cost"],
        },
    )


def ordered_usage(store, row):
    # The ledger's lexical attempt ordering is not execution ordering after fallback.
    return sorted(store.usage(row["id"]), key=lambda r: (r["fallback_hop"], r["retry"]))


def route(row, rows):
    meta = metadata(row)
    out = (json.loads(row["result"]) if row["result"] else {}).get("output", {})
    model = next((m for m in catalog() if m.id == out.get("selected_model")), None)
    selected = (
        {
            "deployment_id": model.deployment,
            "model_version": FIXTURES_VERSION,
            "provider": model.provider,
            "region": REGIONS[model.region],
            "capabilities": [CAPABILITIES[c] for c in model.capabilities],
        }
        if model
        else None
    )
    reasons = {
        "REGION_FORBIDDEN": "REGION",
        "DATA_CLASS_FORBIDDEN": "DATA_POLICY",
        "BUDGET_EXHAUSTED": "BUDGET",
        "UNHEALTHY": "HEALTH",
        "DEADLINE_INCOMPATIBLE": "CAPABILITY",
        "QUALITY_BELOW_FLOOR": "CAPABILITY",
        "CAPABILITY_MISMATCH": "CAPABILITY",
        "LANGUAGE_UNSUPPORTED": "CAPABILITY",
        "CONTEXT_OVERFLOW": "CAPABILITY",
    }
    attempts = []
    for i, r in enumerate(rows, 1):
        state = {
            "healthy": "SUCCEEDED",
            "timeout": "TIMEOUT",
            "429": "RATE_LIMITED",
            "invalid_json": "SCHEMA_INVALID",
        }.get(r["provider_outcome"], "PROVIDER_ERROR")
        attempts.append(
            {
                "deployment_id": r["deployment"],
                "attempt": i,
                "outcome": state,
                "duration_ms": r["modeled_latency_ms"],
                "fallback_reason": None if state == "SUCCEEDED" else r["provider_outcome"],
                "usage": usage(r["usage_id"], [r]),
            }
        )
    rejected = [
        {"deployment_id": f"{c['model']}-fixture", "reason": reasons[reason]}
        for c in out.get("candidate_decisions", [])
        for reason in c["reasons"]
    ]
    if json.loads(row["request"])["input"]["policy_version"] != POLICY_VERSION:
        rejected = [{"deployment_id": m.deployment, "reason": "POLICY_EXPIRED"} for m in catalog()]
    outcome = {"BUDGET_EXHAUSTED": "BUDGET_EXCEEDED"}.get(out.get("outcome"), out.get("outcome"))
    if outcome not in ("SUCCEEDED", "AI_UNAVAILABLE", "POLICY_BLOCKED", "BUDGET_EXCEEDED"):
        outcome = "AI_UNAVAILABLE"
    return checked(
        "route-decision",
        {
            "schema_version": "1.0",
            "operation_id": meta["operation_id"],
            "routing_policy_version": json.loads(row["request"])
            .get("service_request", {})
            .get("routing_policy_version", json.loads(row["request"])["input"]["policy_version"]),
            "outcome": outcome,
            "selected_deployment": selected,
            "rejected_candidates": rejected,
            "attempts": attempts,
            "fallback_kind": ("MODEL" if out.get("model_fallback") else "NONE")
            if selected
            else "HUMAN_REVIEW",
            "mode": MODE,
            "correlation_id": meta["correlation_id"],
            "trace_id": meta["trace_id"],
            "usage": usage(f"usage-{row['id']}", rows),
        },
    )


def usage_record(row, item, index):
    return checked(
        "ai-usage-recorded",
        {
            "operation_id": metadata(row)["operation_id"],
            "provider": item["provider"],
            "deployment": item["deployment"],
            "model_version": metadata(row)["provenance"]["model_version"],
            "region": REGIONS[item["region"]],
            "usage": usage(item["usage_id"], [item]),
            "attempt": index,
        },
    )
