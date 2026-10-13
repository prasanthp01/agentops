"""Explicit private peer profiles. A refused review never authorizes inference or business work."""

from . import canonical_wire as wire
from .catalog import POLICY_VERSION

PROFILE_VERSION = "agentops-peer-profiles-1"
FLOWOPS_REFUSAL = "flowops-review-no-spend-refusal-1"
FLOWOPS_CONSTRAINTS = {
    "application_id": "flowops",
    "environment": "test",
    "mode": wire.MODE,
    "task_type": "order_resolution_review",
    "risk_class": "HIGH",
    "required_capabilities": ["STRUCTURED_OUTPUT"],
    "data_classification": "PUBLIC",
    "allowed_regions": ["eu-test"],
    "latency_deadline_ms": 2000,
    "max_input_tokens": 1024,
    "max_output_tokens": 256,
    "max_cost": 0,
    "currency": "EUR",
    "routing_policy_version": "flowops-sandbox-no-spend-1",
    "extensions": {},
}


def capabilities():
    """App-owned discovery document; this does not extend any frozen canonical schema."""
    return {
        "profile_version": PROFILE_VERSION,
        "project_id": "agentops",
        "contracts_version": "1.0.0",
        "routing_profile": {
            "profile_id": "agentops-synthetic-routing-1",
            "mode": wire.MODE,
            "policy_version": POLICY_VERSION,
            "tasks": ["summarize", "classify", "extract"],
            "risk_classes": ["LOW", "MEDIUM"],
            "capabilities": list(wire.CAPABILITIES.values()),
            "regions": list(wire.REGIONS.values()),
            "currency": "USD",
            "max_cost": 1,
            "context_refs": [],
            "provider_calls": 0,
            "accounting": "SIMULATED_OR_NONE",
            "external_spend": 0,
        },
        "refusal_profiles": [
            {
                "profile_id": FLOWOPS_REFUSAL,
                "constraints": FLOWOPS_CONSTRAINTS,
                "context_binding": "context_refs == [trace_id]; operation_id == inference:<trace_id>",
                "http_status": 200,
                "response_schema": "route-decision",
                "outcome": "POLICY_BLOCKED",
                "fallback_kind": "HUMAN_REVIEW",
                "fallback_owner": "calling_application",
                "business_action_completed": False,
                "context_dereferenced": False,
                "job_created": False,
                "usage_record_created": False,
                "accounting": "NONE",
                "external_spend": 0,
            }
        ],
        "other_unsupported_profiles": {
            "http_status": 403,
            "response_schema": "error",
            "code": "POLICY_BLOCKED",
            "note": "Invalid task/bounds use 422; unsupported mode uses 503. Never retry by relaxing caller constraints.",
        },
        "signed_usage_delivery": {
            "status": "BLOCKED_NOT_IMPLEMENTED",
            "outbox": False,
            "automatic_delivery": False,
            "requirements": [
                "Approved receiver/audience and usage:write grant",
                "Trusted per-environment/tenant signing keys and rotation",
                "Atomic outbox with durable dedup, leases, retries and receipt reconciliation",
            ],
        },
    }


def refusal(request):
    """Called only after canonical validation and authenticated caller binding.

    Return a policy refusal for exactly this known test-only profile. Nothing is fetched,
    routed, reserved, charged or completed. Other profiles retain the existing guards.
    """
    if any(request.get(k) != v for k, v in FLOWOPS_CONSTRAINTS.items()):
        return None
    if (
        request["context_refs"] != [request["trace_id"]]
        or request["operation_id"] != "inference:" + request["trace_id"]
    ):
        return None
    accounting = {
        "usage_id": "refusal-" + wire.sha(request)[:48],
        "kind": "NONE",
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_tokens": 0,
        "provider_calls": 0,
        "cost": {
            "kind": "NONE",
            "value": 0,
            "currency": request["currency"],
            "pricing_version": "no-provider-usage",
        },
    }
    return wire.checked(
        "route-decision",
        {
            "schema_version": "1.0",
            "operation_id": request["operation_id"],
            "routing_policy_version": request["routing_policy_version"],
            "outcome": "POLICY_BLOCKED",
            "selected_deployment": None,
            "rejected_candidates": [],
            "attempts": [],
            "fallback_kind": "HUMAN_REVIEW",
            "mode": request["mode"],
            "correlation_id": request["correlation_id"],
            "trace_id": request["trace_id"],
            "usage": accounting,
        },
    )
