from . import __version__
from .catalog import FIXTURES_VERSION


def failure_result(run_id, code="CANCELLED"):
    return {
        "schema_version": "1.0",
        "project_id": "agentops",
        "run_id": run_id,
        "mode": "SANDBOX_DETERMINISTIC",
        "status": "CANCELLED" if code == "CANCELLED" else "FAILED",
        "app_version": __version__,
        "fixtures_version": FIXTURES_VERSION,
        "output": {
            "outcome": code,
            "connector_kind": "mock",
            "data_provenance": "synthetic",
            "limitations": ["Run ended without a completed result."],
        },
        "evidence": [],
        "errors": [{"code": code, "retryable": False}],
        "cost": {"kind": "NONE", "value": None, "currency": "USD"},
    }
