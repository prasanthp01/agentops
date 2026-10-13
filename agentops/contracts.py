"""Versioned wire contracts. Never echo validation input in API errors."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Mode = Literal["SANDBOX_DETERMINISTIC", "REPLAY_RECORDED", "LIVE_MODEL_AUTHENTICATED"]
Capability = Literal["text", "json", "tools", "vision"]
Region = Literal["EU", "US", "APAC"]
Classification = Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
Failure = Literal["healthy", "429", "503", "timeout", "invalid_json", "context_overflow", "restricted_region"]
Strategy = Literal["lowest_cost", "priority", "fastest", "reliability"]
Identifier = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.:-]+$")]
MODEL_IDS = ("cedar-eu", "harbor-eu", "swift-us", "vault-eu")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SimulationInput(StrictModel):
    task_type: Literal["summarize", "classify", "extract"] = "summarize"
    required_capabilities: list[Capability] = Field(default=["text", "json"], min_length=1, max_length=4)
    allowed_regions: list[Region] = Field(default=["EU"], min_length=1, max_length=3)
    data_classification: Classification = "INTERNAL"
    language: Literal["en", "de", "fr"] = "en"
    latency_deadline_ms: int = Field(default=3000, ge=50, le=15000)
    quality_floor: int = Field(default=80, ge=0, le=100)
    strategy: Strategy = "lowest_cost"
    available_funds_microusd: int = Field(default=20000, ge=0, le=1000000)
    input_tokens: int = Field(default=2000, ge=1, le=100000)
    output_tokens: int = Field(default=500, ge=1, le=8000)
    provider_failures: dict[Literal["cedar-eu", "harbor-eu", "swift-us", "vault-eu"], Failure] = Field(
        default_factory=dict
    )
    unavailable_models: list[Literal["cedar-eu", "harbor-eu", "swift-us", "vault-eu"]] = Field(
        default_factory=list, max_length=4
    )
    max_fallbacks: int = Field(default=2, ge=0, le=3)
    max_retries: int = Field(default=1, ge=0, le=2)
    retry_after_ms: int = Field(default=300, ge=0, le=10000)
    seed: int = Field(default=42, ge=0, le=65535)
    policy_version: Literal["policy-2026-10-v1", "expired-policy"] = "policy-2026-10-v1"
    control_plane_available: bool = True


class SimulationStart(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    project_id: Literal["agentops"] = "agentops"
    mode: Literal["SANDBOX_DETERMINISTIC"] = "SANDBOX_DETERMINISTIC"
    preset_id: Identifier | None = None
    input: SimulationInput
    client_correlation_id: Identifier | None = None


class InferenceRequest(StrictModel):
    """v1.1 explicitly extends the closed packaged v1.0 inference contract."""

    schema_version: Literal["1.1"] = "1.1"
    application_id: Identifier
    environment: Literal["development", "staging", "production"]
    task_type: Literal["summarize", "classify", "extract"]
    required_capabilities: list[Capability] = Field(min_length=1, max_length=4)
    data_classification: Classification
    allowed_regions: list[Region] = Field(min_length=1, max_length=3)
    latency_deadline_ms: int = Field(ge=50, le=15000)
    max_cost_microusd: int = Field(ge=0, le=1000000)
    currency: Literal["USD"] = "USD"
    routing_policy_version: Literal["policy-2026-10-v1"]
    correlation_id: Identifier
    operation_id: Identifier
    mode: Literal["SANDBOX_DETERMINISTIC", "LIVE_MODEL_AUTHENTICATED"]
    quality_floor: int = Field(default=80, ge=0, le=100)
    input_tokens: int = Field(ge=1, le=100000)
    output_tokens: int = Field(ge=1, le=8000)
    language: Literal["en", "de", "fr"] = "en"
    strategy: Strategy = "lowest_cost"
    max_fallbacks: int = Field(default=2, ge=0, le=3)
    max_retries: int = Field(default=1, ge=0, le=2)
    # Only synthetic bounded tasks are accepted by the current gateway. No raw prompts stored.
    synthetic_task: Literal["service-status-summary"] = "service-status-summary"

    def simulation(self) -> SimulationInput:
        return SimulationInput(
            **{k: v for k, v in self.model_dump().items() if k in SimulationInput.model_fields},
            available_funds_microusd=self.max_cost_microusd,
            policy_version=self.routing_policy_version,
        )


class Model(StrictModel):
    id: Identifier
    provider: Identifier
    deployment: Identifier
    region: Region
    capabilities: list[Capability]
    languages: list[Literal["en", "de", "fr"]]
    classifications: list[Classification]
    max_context: int = Field(ge=1)
    quality_score: int = Field(ge=0, le=100)
    quality_evidence: str
    input_microusd_per_1k: int = Field(ge=0)
    output_microusd_per_1k: int = Field(ge=0)
    pricing_version: str
    pricing_date: str
    latency_ms: int = Field(ge=1)
    reliability: int = Field(ge=0, le=100)
    priority: int
    synthetic: bool = True

    def price(self, input_tokens: int, output_tokens: int) -> int:
        # Integer microdollars, rounding each attempted call upward.
        return (
            self.input_microusd_per_1k * input_tokens + self.output_microusd_per_1k * output_tokens + 999
        ) // 1000


class RunState(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    project_id: Literal["agentops"] = "agentops"
    run_id: str
    mode: Mode
    status: Literal["ACCEPTED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"]
    created_at: str
    completed_at: str | None
    event_count: int
    result_reference: str


class ProviderOutput(StrictModel):
    decision: Literal["accepted"]
    task_type: Literal["summarize", "classify", "extract"]
    synthetic: Literal[True]


class ServiceCredential(StrictModel):
    application_id: Identifier
    token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    environments: list[Literal["development", "staging", "production"]] = Field(min_length=1)
    scopes: list[Literal["inference:write", "usage:read", "policy:read"]] = Field(min_length=1)
    budget_microusd: int = Field(ge=0, le=1000000000)


class PrivateConfig(StrictModel):
    credentials: list[ServiceCredential] = Field(default_factory=list, max_length=20)
    global_budget_microusd: int = Field(default=0, ge=0, le=1000000000)

    @model_validator(mode="after")
    def unique_apps(self):
        if len({c.application_id for c in self.credentials}) != len(self.credentials):
            raise ValueError("Duplicate application IDs")
        return self


class UsageRecord(StrictModel):
    usage_id: str = Field(min_length=1, max_length=160)
    operation_id: str = Field(min_length=1, max_length=100)
    attempt_id: Identifier
    model: Identifier
    provider: Identifier
    deployment: Identifier
    region: Region
    application_id: Identifier
    environment: Literal["sandbox", "test", "development", "staging", "production"]
    task: Literal["summarize", "classify", "extract"]
    kind: Literal["SIMULATED"]
    input_tokens: int = Field(ge=0, le=100000)
    output_tokens: int = Field(ge=0, le=8000)
    cost_microusd: int = Field(ge=0)
    currency: Literal["USD"]
    pricing_version: Identifier
    pricing_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    provider_outcome: Failure
    retry: int = Field(ge=0, le=2)
    fallback_hop: int = Field(ge=0, le=3)
    modeled_latency_ms: int = Field(ge=0)
    external_llm_spend_microusd: Literal[0] = 0
