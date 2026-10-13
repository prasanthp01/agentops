"""Deterministic policy-first routing. The execution path has no network adapter."""

import time
from dataclasses import dataclass

from pydantic import ValidationError

from . import __version__
from .catalog import FIXTURES_VERSION, POLICY_VERSION, catalog
from .contracts import Model, ProviderOutput, SimulationInput, UsageRecord
from .store import LeaseLost, Store, StoreError


@dataclass
class Circuit:
    failures: int = 0
    opened_at_ms: int | None = None
    cooldown_ms: int = 1000

    def allows(self, now_ms):
        return self.opened_at_ms is None or now_ms - self.opened_at_ms >= self.cooldown_ms

    def record(self, failure, now_ms):
        if failure in ("503", "timeout"):
            self.failures += 1
            if self.failures >= 2:
                self.opened_at_ms = now_ms
        elif failure == "healthy":
            self.failures, self.opened_at_ms = 0, None


def filter_candidates(request: SimulationInput, models=None):
    approved, decisions = [], []
    for model in models or catalog():
        reasons = []
        if model.region not in request.allowed_regions:
            reasons.append("REGION_FORBIDDEN")
        if request.data_classification not in model.classifications:
            reasons.append("DATA_CLASS_FORBIDDEN")
        if not set(request.required_capabilities) <= set(model.capabilities):
            reasons.append("CAPABILITY_MISMATCH")
        if request.language not in model.languages:
            reasons.append("LANGUAGE_UNSUPPORTED")
        if request.input_tokens + request.output_tokens > model.max_context:
            reasons.append("CONTEXT_OVERFLOW")
        if model.quality_score < request.quality_floor:
            reasons.append("QUALITY_BELOW_FLOOR")
        if model.id in request.unavailable_models:
            reasons.append("UNHEALTHY")
        if model.latency_ms > request.latency_deadline_ms:
            reasons.append("DEADLINE_INCOMPATIBLE")
        cost = model.price(request.input_tokens, request.output_tokens)
        if cost > request.available_funds_microusd:
            reasons.append("BUDGET_EXHAUSTED")
        decisions.append(
            {
                "model": model.id,
                "eligible": not reasons,
                "reasons": reasons,
                "predicted_cost_microusd": cost,
                "quality_score": model.quality_score,
                "modeled_latency_ms": model.latency_ms,
                "synthetic": True,
                "region": model.region,
            }
        )
        if not reasons:
            approved.append(model)

    def score(model):
        return {
            "lowest_cost": model.price(request.input_tokens, request.output_tokens),
            "priority": model.priority,
            "fastest": model.latency_ms,
            "reliability": -model.reliability,
        }[request.strategy], model.id

    return sorted(approved, key=score), decisions


def invoke_mock(model: Model, request: SimulationInput, attempt: int):
    failure = request.provider_failures.get(model.id, "healthy")
    # Temporary rate limit resolves only on the next attempt, after Retry-After.
    if failure == "429" and attempt > 0:
        failure = "healthy"
    output = {"decision": "accepted", "task_type": request.task_type, "synthetic": True}
    if failure == "invalid_json":
        output = {"unexpected": "Malformed synthetic fixture"}
    return failure, output


class Engine:
    def __init__(self, store: Store):
        self.store = store

    def execute(self, job):
        run_id, claim = job["id"], job["claim"]
        request = SimulationInput.model_validate(job["request"]["input"])
        start = time.perf_counter()
        elapsed, attempts, fallback_count = 0, 0, 0
        selected, validated, decisions = None, None, []
        outcome, reason = "AI_UNAVAILABLE", "No provider completed this request."

        def emit(key, title, overview, technical=None, status="SUCCEEDED", usage=None):
            if time.time() - job["created"] > self.store.settings.max_run_seconds:
                raise StoreError("RUN_TIMEOUT")
            self.store.heartbeat(run_id, claim)
            event = {
                "schema_version": "1.0",
                "run_id": run_id,
                "project_id": "agentops",
                "step_id": key,
                "step_type": key.split(":")[0],
                "component": "routing-engine",
                "status": status,
                "mode": "SANDBOX_DETERMINISTIC",
                "title": title,
                "overview": overview,
                "technical": technical or {},
                "trace_id": run_id,
                "duration_ms": None,
                "usage": usage or {"kind": "NONE"},
            }
            self.store.emit(run_id, claim, key, event)

        emit(
            "authorize",
            "Scope verified",
            "This operation belongs to its authenticated caller.",
            {
                "caller_kind": "service" if job["owner"].startswith("app:") else "anonymous-session",
                "mode": "SANDBOX_DETERMINISTIC",
            },
        )
        if request.policy_version != POLICY_VERSION:
            outcome, reason = "POLICY_BLOCKED", "The local policy has expired. No provider call is allowed."
            emit("policy_check", "Policy expired", reason, {"code": "POLICY_EXPIRED"}, "BLOCKED")
        elif not request.control_plane_available:
            outcome, reason = (
                "AI_UNAVAILABLE",
                "AgentOps is unavailable. Shared spending limits cannot be verified; billable inference is denied.",
            )
            emit(
                "policy_check",
                "Fail-safe mode",
                reason,
                {
                    "code": "CONTROL_PLANE_UNAVAILABLE",
                    "cached_policy": POLICY_VERSION,
                    "distributed_budget_provable": False,
                    "direct_provider_allowed": False,
                },
                "BLOCKED",
            )
        else:
            emit(
                "policy_check",
                "Policy accepted",
                "Region, data sensitivity, capabilities, and budget are enforced before ranking.",
                {"policy_version": POLICY_VERSION, "strategy": request.strategy},
            )
            candidates, decisions = filter_candidates(request)
            emit(
                "catalog_filter",
                "Eligibility checked",
                f"{len(candidates)} of {len(catalog())} synthetic models meet every hard constraint.",
                {"candidates": decisions},
            )
            emit(
                "candidate_scores",
                "Candidates ranked",
                "Only eligible models participate in selection.",
                {"ordered_models": [m.id for m in candidates], "strategy": request.strategy},
            )
            if not candidates:
                outcome = (
                    "BUDGET_EXHAUSTED"
                    if any("BUDGET_EXHAUSTED" in d["reasons"] for d in decisions)
                    else "POLICY_BLOCKED"
                )
                if all("UNHEALTHY" in d["reasons"] for d in decisions):
                    outcome = "AI_UNAVAILABLE"
                reason = "No model meets all of the requested constraints."
            for index, model in enumerate(candidates[: request.max_fallbacks + 1]):
                circuit = Circuit()
                if index:
                    emit(
                        f"retry_or_fallback:model:{index}",
                        "Compatible fallback",
                        f"Try {model.id}; the same hard policy still applies.",
                        {
                            "from": candidates[index - 1].id,
                            "to": model.id,
                            "fallback_hop": index,
                            "reason": reason,
                        },
                    )
                for retry in range(request.max_retries + 1):
                    self.store.heartbeat(run_id, claim)
                    if not circuit.allows(elapsed):
                        emit(
                            f"circuit:{model.id}",
                            "Circuit opened",
                            f"{model.id} is suspended after repeated transport failures.",
                            {
                                "model": model.id,
                                "failures": circuit.failures,
                                "cooldown_ms": circuit.cooldown_ms,
                            },
                            "BLOCKED",
                        )
                        break
                    if elapsed + model.latency_ms > request.latency_deadline_ms:
                        reason = "The remaining deadline cannot accommodate another attempt."
                        emit(
                            f"deadline:{model.id}:{retry}",
                            "Deadline reached",
                            reason,
                            {"modeled_elapsed_ms": elapsed},
                            "BLOCKED",
                        )
                        break
                    attempt_id = f"{model.id}:{retry}"
                    ceiling = model.price(request.input_tokens, request.output_tokens)
                    try:
                        self.store.reserve(run_id, claim, attempt_id, ceiling)
                    except StoreError as exc:
                        if isinstance(exc, LeaseLost):
                            raise
                        outcome, reason = (
                            "BUDGET_EXHAUSTED",
                            "A transactional budget reservation was refused.",
                        )
                        emit(
                            f"budget_reserve:{attempt_id}",
                            "Budget protected",
                            reason,
                            {"code": exc.code},
                            "BLOCKED",
                        )
                        break
                    emit(
                        f"budget_reserve:{attempt_id}",
                        "Budget reserved",
                        f"Reserved ${ceiling / 1000000:.6f} in simulated funds.",
                        {"attempt_id": attempt_id, "reserved_microusd": ceiling, "kind": "SIMULATED"},
                    )
                    failure, response = invoke_mock(model, request, retry)
                    elapsed += model.latency_ms
                    attempts += 1
                    fallback_count = index
                    emit(
                        f"invoke_mock_provider:{attempt_id}",
                        "Mock provider called",
                        f"{model.id} returned {failure}.",
                        {
                            "model": model.id,
                            "attempt": retry + 1,
                            "response_kind": failure,
                            "modeled_latency_ms": model.latency_ms,
                            "adapter": "mock",
                        },
                        "SUCCEEDED" if failure == "healthy" else "FAILED",
                    )
                    circuit.record(failure, elapsed)
                    input_tokens = (
                        request.input_tokens if failure in ("healthy", "invalid_json", "timeout") else 0
                    )
                    output_tokens = request.output_tokens if failure in ("healthy", "invalid_json") else 0
                    price = model.price(input_tokens, output_tokens)
                    ledger = {
                        "usage_id": f"{run_id}:{attempt_id}",
                        "operation_id": job["operation_id"],
                        "attempt_id": attempt_id,
                        "model": model.id,
                        "provider": model.provider,
                        "deployment": model.deployment,
                        "region": model.region,
                        "application_id": job["owner"][4:]
                        if job["owner"].startswith("app:")
                        else "public-sandbox",
                        "environment": job["request"]
                        .get("service_request", {})
                        .get("environment", "sandbox"),
                        "task": request.task_type,
                        "kind": "SIMULATED",
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cost_microusd": price,
                        "currency": "USD",
                        "pricing_version": model.pricing_version,
                        "pricing_date": model.pricing_date,
                        "provider_outcome": failure,
                        "retry": retry,
                        "fallback_hop": index,
                        "modeled_latency_ms": model.latency_ms,
                        "external_llm_spend_microusd": 0,
                    }
                    ledger = UsageRecord.model_validate(ledger).model_dump()
                    self.store.settle(run_id, claim, attempt_id, price, ledger)
                    emit(
                        f"usage_record:{attempt_id}",
                        "Usage recorded",
                        "This attempt is counted once, including any failed-call overhead.",
                        ledger,
                        usage={
                            "kind": "SIMULATED",
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "estimated_cost": price / 1000000,
                            "currency": "USD",
                        },
                    )
                    if failure in ("healthy", "invalid_json"):
                        try:
                            validated = ProviderOutput.model_validate(response).model_dump()
                            selected, fallback_count = model.id, index
                            emit(
                                f"validate_output:{attempt_id}",
                                "Output validated",
                                "The synthetic result matches the task output contract.",
                                {"output": validated, "schema": "ProviderOutput-v1"},
                            )
                            outcome, reason = (
                                "SUCCEEDED",
                                f"{model.id} completed the synthetic request within policy.",
                            )
                            break
                        except ValidationError:
                            reason = "The provider output failed schema validation."
                            emit(
                                f"validate_output:{attempt_id}",
                                "Output rejected",
                                reason,
                                {"code": "INVALID_OUTPUT"},
                                "FAILED",
                            )
                            break
                    reason = f"{model.id} failed with {failure}."
                    if failure not in ("429", "503", "timeout") or retry >= request.max_retries:
                        break
                    wait = (
                        request.retry_after_ms
                        if failure == "429"
                        else 100 * 2**retry + (request.seed + retry) % 37
                    )
                    if elapsed + wait + model.latency_ms > request.latency_deadline_ms:
                        reason = "Retry-After or backoff would exceed the operation deadline."
                        break
                    emit(
                        f"retry_or_fallback:retry:{attempt_id}",
                        "Retry scheduled",
                        f"Honor a {wait} ms modeled wait before the next mock call.",
                        {
                            "reason": failure,
                            "wait_ms": wait,
                            "retry_after_honored": failure == "429",
                            "clock": "virtual",
                        },
                    )
                    elapsed += wait
                if selected:
                    break
        usage = self.store.usage(run_id)
        total = sum(row["cost_microusd"] for row in usage)
        workflow_fallback = (
            None
            if selected
            else {
                "kind": "DETERMINISTIC_OR_HUMAN",
                "owner": "calling_application",
                "executed": False,
                "required": True,
            }
        )
        output = {
            "outcome": outcome,
            "reason": reason,
            "selected_model": selected,
            "provider_output": validated,
            "candidate_decisions": decisions,
            "request_policy": request.model_dump(),
            "model_fallback": fallback_count > 0,
            "fallback_hops": fallback_count,
            "workflow_fallback": workflow_fallback,
            "attempt_count": attempts,
            "modeled_latency_ms": elapsed,
            "usage": usage,
            "total_cost_microusd": total,
            "external_llm_spend_microusd": 0,
            "safety_status": "POLICY_ENFORCED",
            "connector_kind": "mock",
            "data_provenance": "bounded synthetic catalog and token counts",
            "versions": {
                "policy": request.policy_version,
                "catalog": FIXTURES_VERSION,
                "output_schema": "1.0",
                "seed": request.seed,
            },
            "warnings": [],
            "limitations": [
                "No real model quality or provider latency is measured.",
                "Workflow fallback must be performed by the calling application.",
            ],
        }
        emit(
            "result",
            "Routing complete" if selected else "Safe fallback required",
            reason,
            output,
            status="SUCCEEDED" if selected else "BLOCKED",
        )
        result = {
            "schema_version": "1.0",
            "project_id": "agentops",
            "run_id": run_id,
            "mode": "SANDBOX_DETERMINISTIC",
            "status": "SUCCEEDED" if selected else "FAILED",
            "app_version": __version__,
            "fixtures_version": FIXTURES_VERSION,
            "output": output,
            "evidence": [
                {
                    "kind": "trace",
                    "run_id": run_id,
                    "reference": f"/api/projects/agentops/simulations/{run_id}/events",
                }
            ],
            "errors": []
            if selected
            else [{"code": outcome, "message": reason, "retryable": outcome == "AI_UNAVAILABLE"}],
            "duration_ms": round((time.perf_counter() - start) * 1000, 3),
            "cost": {"kind": "SIMULATED", "value": total / 1000000, "currency": "USD"},
        }
        self.store.finish(run_id, claim, result)
        return result
