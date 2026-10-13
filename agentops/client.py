"""Small caller SDK. Failure is a handoff decision, never a completed business result."""

from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from pydantic import Field

from .contracts import Identifier, InferenceRequest, StrictModel


class CachedPolicy(StrictModel):
    schema_version: str = "1.1"
    policy_version: Identifier
    application_id: Identifier
    expires_at: datetime
    direct_provider_allowed: bool = False
    budget_authority: str = Field(default="central", pattern="^central$")


def local_fallback(policy: CachedPolicy, application_id: str, now=None):
    now = now or datetime.now(UTC)
    valid = (
        policy.expires_at.tzinfo is not None
        and policy.expires_at > now
        and policy.application_id == application_id
    )
    return {
        "outcome": "AI_UNAVAILABLE" if valid else "POLICY_BLOCKED",
        "reason": "CENTRAL_QUOTA_UNAVAILABLE" if valid else "POLICY_EXPIRED_OR_SCOPE_MISMATCH",
        "billable_inference_allowed": False,
        "workflow_fallback": "LOCAL_DETERMINISTIC_OR_HUMAN",
        "business_action_completed": False,
    }


class AgentOpsClient:
    def __init__(self, base_url: str, token: str, *, transport=None):
        parsed = urlparse(base_url)
        local_http = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")
        if (
            (parsed.scheme != "https" and not local_http)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Use HTTPS or loopback HTTP with no embedded credentials, query, or fragment")
        self.http = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": "Bearer " + token},
            timeout=5,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def submit(self, request: InferenceRequest):
        response = self.http.post("inferences", json=request.model_dump())
        response.raise_for_status()
        return response.json()

    def result(self, run_id: str):
        response = self.http.get(f"simulations/{run_id}/result")
        response.raise_for_status()
        return response.json()

    def close(self):
        self.http.close()
