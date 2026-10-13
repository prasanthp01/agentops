"""Generate machine-readable contracts from the actual application, without running workers."""

import json
import tempfile
from pathlib import Path

from agentops.app import create_app
from agentops.client import CachedPolicy
from agentops.contracts import (
    InferenceRequest,
    Model,
    PrivateConfig,
    ProviderOutput,
    RunState,
    SimulationInput,
    SimulationStart,
    UsageRecord,
)
from agentops.settings import Settings

root = Path("contracts")
root.mkdir(exist_ok=True)
for cls in [
    InferenceRequest,
    Model,
    PrivateConfig,
    ProviderOutput,
    RunState,
    SimulationInput,
    SimulationStart,
    UsageRecord,
    CachedPolicy,
]:
    schema = cls.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    (root / (cls.__name__ + ".schema.json")).write_text(json.dumps(schema, indent=2) + "\n")
with tempfile.TemporaryDirectory() as temp:
    app = create_app(Settings(db_path=Path(temp) / "contract.db", workers=0))
    (root / "openapi.json").write_text(json.dumps(app.openapi(), indent=2) + "\n")
print("Exported contracts and OpenAPI.")
