import json

import httpx
import pytest

from agentops.client import AgentOpsClient
from agentops.contracts import InferenceRequest
from tests.test_api import service_body


def test_sdk_prefix_and_auth():
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(
            202 if request.method == "POST" else 200, json={"run_id": "run1", "status": "ACCEPTED"}
        )

    client = AgentOpsClient(
        "https://agentops.example/api/projects/agentops",
        "synthetic-test-token",
        transport=httpx.MockTransport(handle),
    )
    result = client.submit(InferenceRequest.model_validate(service_body()))
    assert result["run_id"] == "run1"
    client.result("run1")
    client.close()
    assert seen[0].url.path == "/api/projects/agentops/inferences"
    assert seen[1].url.path == "/api/projects/agentops/simulations/run1/result"
    assert seen[0].headers["authorization"] == "Bearer synthetic-test-token"
    assert json.loads(seen[0].content)["operation_id"] == "operation1"


@pytest.mark.parametrize(
    "url", ["http://remote.example/api", "https://user:pass@example.test", "https://example.test?token=x"]
)
def test_sdk_refuses_insecure_credentials(url):
    with pytest.raises(ValueError):
        AgentOpsClient(url, "synthetic")
