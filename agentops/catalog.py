"""Fictional provider fixtures; no catalog entry represents a real model."""

from .contracts import Model, SimulationInput

FIXTURES_VERSION = "synthetic-catalog-1.0.0"
POLICY_VERSION = "policy-2026-10-v1"


def catalog() -> list[Model]:
    rows = [
        (
            "cedar-eu",
            "Cedar",
            "EU",
            ["text", "json"],
            ["en", "de"],
            ["PUBLIC", "INTERNAL"],
            16000,
            84,
            80,
            240,
            420,
            96,
            1,
        ),
        (
            "harbor-eu",
            "Harbor",
            "EU",
            ["text", "json", "tools"],
            ["en", "de", "fr"],
            ["PUBLIC", "INTERNAL", "CONFIDENTIAL"],
            32000,
            93,
            180,
            600,
            760,
            99,
            2,
        ),
        (
            "swift-us",
            "Swift",
            "US",
            ["text", "json", "vision"],
            ["en"],
            ["PUBLIC", "INTERNAL"],
            8000,
            78,
            40,
            120,
            180,
            92,
            3,
        ),
        (
            "vault-eu",
            "Vault",
            "EU",
            ["text", "json", "tools", "vision"],
            ["en", "de", "fr"],
            ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"],
            128000,
            97,
            600,
            1800,
            1250,
            99,
            4,
        ),
    ]
    return [
        Model(
            id=id_,
            provider=provider.lower(),
            deployment=f"{id_}-fixture",
            region=region,
            capabilities=caps,
            languages=langs,
            classifications=classes,
            max_context=context,
            quality_score=quality,
            quality_evidence=FIXTURES_VERSION,
            input_microusd_per_1k=inp,
            output_microusd_per_1k=out,
            pricing_version="synthetic-usd-v1",
            pricing_date="2026-10-13",
            latency_ms=latency,
            reliability=reliability,
            priority=priority,
        )
        for id_, provider, region, caps, langs, classes, context, quality, inp, out, latency, reliability, priority in rows
    ]


def presets() -> list[dict]:
    specs = [
        ("economical", "Economical route", "Choose the least expensive eligible model.", {}),
        (
            "outage",
            "Primary outage",
            "Cedar fails; Harbor remains eligible.",
            {"provider_failures": {"cedar-eu": "503"}},
        ),
        (
            "rate-limit",
            "429 · Retry-After",
            "Honor a temporary rate limit before retrying.",
            {"provider_failures": {"cedar-eu": "429"}},
        ),
        ("region", "Prohibited region", "No fixture is approved for APAC.", {"allowed_regions": ["APAC"]}),
        (
            "budget",
            "Budget exhausted",
            "Refuse a call before spending beyond its budget.",
            {"available_funds_microusd": 0},
        ),
        (
            "malformed",
            "Malformed response",
            "Validate output and try a compatible model.",
            {"provider_failures": {"cedar-eu": "invalid_json"}},
        ),
        (
            "all-down",
            "All providers unavailable",
            "Return AI_UNAVAILABLE to the caller.",
            {"provider_failures": {m.id: "503" for m in catalog()}},
        ),
        (
            "agentops-down",
            "AgentOps outage",
            "Deny inference when shared quotas cannot be verified.",
            {"control_plane_available": False},
        ),
        (
            "expired",
            "Expired local policy",
            "Reject stale policy even when a model is healthy.",
            {"policy_version": "expired-policy"},
        ),
    ]
    return [
        {
            "id": id_,
            "title": title,
            "description": description,
            "input": SimulationInput(**overrides).model_dump(),
        }
        for id_, title, description, overrides in specs
    ]
