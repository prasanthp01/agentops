"""Opt-in Contracts v1 API using the existing durable routing engine and ledger."""

import asyncio
import json
import os
import re
import secrets
import sqlite3
import subprocess
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field, ValidationError, model_validator
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException

from . import canonical_wire as wire
from . import peer_profiles
from .catalog import FIXTURES_VERSION, POLICY_VERSION
from .contracts import PrivateConfig, ServiceCredential, SimulationInput, StrictModel
from .settings import Settings
from .store import Store, StoreError, digest
from .vendor.ecosystem_contracts import ContractError
from .worker import Workers

APP_VERSION = "0.2.0-contracts.2"
PUBLIC_PREFIX = "/api/projects/agentops"
COOKIE = "agentops_contract_session"
ID = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$"


class Credential(StrictModel):
    application_id: Literal["resolveai", "flowops", "knowledgeops", "agentops"]
    environment: Literal["test", "development", "staging", "production"]
    tenant_id: str = Field(pattern=ID)
    audience: Literal["agentops"]
    token_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scopes: list[Literal["inference:request", "usage:read"]] = Field(min_length=1, max_length=2)
    budget_microusd: int = Field(ge=0, le=1000000000)

    def binding(self):
        return self.model_dump(include={"application_id", "environment", "tenant_id", "audience"})


class Config(StrictModel):
    credentials: list[Credential] = Field(default_factory=list, max_length=20)
    global_budget_microusd: int = Field(default=0, ge=0, le=1000000000)

    @model_validator(mode="after")
    def unique(self):
        # Existing accounts are application scoped. Never imply tenant-specific budgets.
        if len({c.application_id for c in self.credentials}) != len(self.credentials):
            raise ValueError("Only one tenant/environment binding per application is supported")
        if len({c.token_sha256 for c in self.credentials}) != len(self.credentials):
            raise ValueError("Each credential requires a distinct token")
        return self


class BoundaryError(Exception):
    def __init__(self, code):
        self.code = code


def response_error(code):
    body = wire.error(code)
    return JSONResponse(
        body,
        body["http_status"],
        headers={"Cache-Control": "no-store", **({"Retry-After": "60"} if code == "RATE_LIMITED" else {})},
    )


class Boundary:
    """Bound body bytes before parsing; never reflect invalid body values or property names."""

    def __init__(self, app, allowed_hosts):
        self.app = app
        self.allowed_hosts = allowed_hosts

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        host = headers.get(b"host", b"").decode().split(":")[0]
        if host not in self.allowed_hosts:
            return await response_error("FORBIDDEN")(scope, receive, send)
        if scope["method"] in ("POST", "PUT", "PATCH"):
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 16384:
                    return await response_error("PAYLOAD_TOO_LARGE")(scope, receive, send)
                if not message.get("more_body"):
                    break
            delivered = False
            original = receive

            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await original()

            receive = bounded_receive

        async def no_cache(message):
            if message["type"] == "http.response.start":
                message["headers"] = [(k, v) for k, v in message.get("headers", []) if k != b"cache-control"]
                message["headers"] += [
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ]
            await send(message)

        await self.app(scope, receive, no_cache)


class ContractStore(Store):
    def get(self, run_id, owner, *, binding=None):
        with self.connection() as con:
            row = con.execute("SELECT * FROM runs WHERE id=? AND owner=?", (run_id, owner)).fetchone()
        if not row or "contract_v1" not in json.loads(row["request"]):
            raise StoreError("RUN_NOT_FOUND", 404)
        if wire.metadata(row).get("binding") != binding:
            raise StoreError("RUN_NOT_FOUND", 404)
        if row["expires"] <= time.time():
            raise StoreError("RUN_EXPIRED", 410)
        return dict(row)

    def count(self, run_id):
        with self.connection() as con:
            return con.execute("SELECT COUNT(*) FROM events WHERE run_id=?", (run_id,)).fetchone()[0]


def create_app(settings=None, config=None, source_revision=None, prefix=""):
    settings = settings or Settings.from_env()
    config_path = os.getenv("AGENTOPS_CONTRACT_CONFIG")
    config = config or (
        Config.model_validate_json(Path(config_path).read_text()) if config_path else Config()
    )
    source_revision = source_revision or os.getenv("AGENTOPS_SOURCE_REVISION")
    if not source_revision:
        source_revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1], text=True
        ).strip()
    if not re.fullmatch(r"[a-f0-9]{40}", source_revision):
        raise ValueError("AGENTOPS_SOURCE_REVISION must identify the tested source")
    settings = replace(
        settings,
        max_active=min(settings.max_active, 16),
        max_per_owner=min(settings.max_per_owner, 4),
        max_run_seconds=min(settings.max_run_seconds, 120),
        run_ttl_seconds=min(max(settings.run_ttl_seconds, 60), 3600),
        private=PrivateConfig(
            global_budget_microusd=config.global_budget_microusd,
            credentials=[
                ServiceCredential(
                    application_id=c.application_id,
                    token_sha256=c.token_sha256,
                    environments=["development" if c.environment == "test" else c.environment],
                    scopes=["inference:write", "usage:read"],
                    budget_microusd=c.budget_microusd,
                )
                for c in config.credentials
            ],
        ),
    )
    store = ContractStore(settings)
    workers = Workers(store)

    @asynccontextmanager
    async def lifespan(app):
        workers.start()
        try:
            yield
        finally:
            workers.close()

    app = FastAPI(
        title="AgentOps Contracts v1",
        version=APP_VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(Boundary, allowed_hosts=settings.allowed_hosts)
    app.state.store, app.state.settings = store, settings
    app.state.streams = {}
    router = APIRouter(prefix=prefix)
    provenance = {
        "data_origin": "SYNTHETIC",
        "connector_kind": "MOCK",
        "app_version": APP_VERSION,
        "fixtures_version": FIXTURES_VERSION,
        "rules_version": POLICY_VERSION,
        "workflow_version": "agentops-routing-v1",
        "model_version": FIXTURES_VERSION,
        "prompt_version": "none",
        "source_revision": source_revision,
    }

    @app.exception_handler(BoundaryError)
    async def boundary_error(request, exc):
        return response_error(exc.code)

    @app.exception_handler(ContractError)
    @app.exception_handler(ValidationError)
    async def invalid(request, exc):
        return response_error("VALIDATION_ERROR")

    @app.exception_handler(StoreError)
    async def storage_error(request, exc):
        code = {
            401: "UNAUTHENTICATED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "CONFLICT",
            410: "RUN_EXPIRED",
            429: "RATE_LIMITED",
            402: "BUDGET_EXCEEDED",
        }.get(exc.status, "SERVICE_UNAVAILABLE")
        return response_error(code)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return response_error("NOT_FOUND" if exc.status_code in (404, 405) else "VALIDATION_ERROR")

    @app.exception_handler(sqlite3.Error)
    @app.exception_handler(Exception)
    async def unavailable(request, exc):
        return response_error("SERVICE_UNAVAILABLE")

    def origin(request):
        expected = settings.public_origin or (
            ("https" if settings.secure_cookies else request.url.scheme) + "://" + request.headers["host"]
        )
        if (
            request.headers.get("origin") not in (None, expected)
            or request.headers.get("sec-fetch-site") == "cross-site"
        ):
            raise BoundaryError("FORBIDDEN")

    def bearer(request):
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or len(auth) > 300:
            raise BoundaryError("UNAUTHENTICATED")
        return auth[7:]

    def service(request, scope):
        token_hash = digest(bearer(request))
        for c in config.credentials:
            if secrets.compare_digest(token_hash, c.token_sha256):
                if scope not in c.scopes:
                    raise BoundaryError("FORBIDDEN")
                return "app:" + c.application_id, c
        raise BoundaryError("UNAUTHENTICATED")

    def owner(request, mutate=False, scope="inference:request"):
        if request.headers.get("authorization"):
            token = bearer(request)
            if token.startswith("sandbox."):
                return store.authenticate_session(token), None
            return service(request, scope)
        if mutate:
            origin(request)
        token = request.cookies.get(COOKIE)
        if not token or not token.startswith("sandbox."):
            raise BoundaryError("UNAUTHENTICATED")
        return store.authenticate_session(
            token, request.headers.get("x-csrf-token", "") if mutate else None
        ), None

    def read(run_id, request, mutate=False, scope="inference:request"):
        identity, credential = owner(request, mutate, scope)
        row = store.get(run_id, identity, binding=credential.binding() if credential else None)
        return row

    async def body(request, name):
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise BoundaryError("VALIDATION_ERROR")
        try:
            value = await request.json()
            wire.REGISTRY.validate(name, value)
        except (ValueError, RecursionError, TypeError):
            raise BoundaryError("VALIDATION_ERROR") from None
        return value

    def terminal(row):
        if row["status"] in ("ACCEPTED", "RUNNING"):
            raise BoundaryError("CONFLICT")
        return row

    def envelope(inp, correlation, operation, trace, binding=None):
        return {
            "schema_version": "1.0",
            "project_id": "agentops",
            "mode": wire.MODE,
            "input": inp,
            "contract_v1": {
                "correlation_id": correlation,
                "operation_id": operation,
                "trace_id": trace,
                "binding": binding,
                "provenance": provenance,
            },
        }

    @app.get("/healthz")
    @router.get("/healthz")
    def health():
        with store.connection() as con:
            con.execute("SELECT 1")
        return {
            "status": "ok",
            "version": APP_VERSION,
            "contracts_version": "1.0.0",
            "source_revision": source_revision,
            "live_inference_enabled": False,
        }

    @router.get("/simulation/schema")
    def schema():
        return wire.schema(settings)

    @router.get("/simulation/presets")
    def presets():
        return wire.preset_list()

    @router.post("/simulation/session")
    def session(request: Request):
        origin(request)
        transport = request.query_params.get("transport", "bearer")
        if transport not in ("bearer", "cookie"):
            raise BoundaryError("VALIDATION_ERROR")
        existing = request.cookies.get(COOKIE) if transport == "cookie" else None
        try:
            if not existing or not existing.startswith("sandbox."):
                raise StoreError("SESSION_REQUIRED", 401)
            csrf, ttl = store.resume_session(existing)
            token = existing
        except StoreError:
            token, csrf = store.session(
                request.client.host if request.client else "unknown", token_prefix="sandbox."
            )
            ttl = settings.session_ttl_seconds
        result = {"expires_in_seconds": ttl, "token_type": "Bearer" if transport == "bearer" else "Cookie"}
        result.update({"token": token} if transport == "bearer" else {"csrf_token": csrf})
        response = JSONResponse(result)
        if transport == "cookie":
            response.set_cookie(
                COOKIE,
                token,
                httponly=True,
                secure=settings.secure_cookies,
                samesite="strict",
                path=prefix or PUBLIC_PREFIX,
                max_age=ttl,
            )
        return response

    @router.post("/simulations", status_code=202)
    async def start(request: Request):
        identity, credential = owner(request, True)
        if credential:
            raise BoundaryError("FORBIDDEN")
        value = await body(request, "simulation-start")
        if value["project_id"] != "agentops" or value["mode"] != wire.MODE or value.get("extensions"):
            raise BoundaryError("VALIDATION_ERROR")
        if value.get("preset_id") and value["preset_id"] not in {
            p["preset_id"] for p in wire.preset_list()["presets"]
        }:
            raise BoundaryError("VALIDATION_ERROR")
        inp = wire.to_domain_input(value["input"], value["seed"])
        key = request.headers.get("idempotency-key", "op-" + secrets.token_hex(16))
        if not re.fullmatch(ID, key):
            raise BoundaryError("VALIDATION_ERROR")
        env = envelope(inp, value["client_correlation_id"], key, value["client_correlation_id"])
        env["contract_v1"]["request"] = value
        run_id, _ = store.enqueue(identity, key, env)
        return wire.run(store.get(run_id, identity), store.count(run_id))

    @router.get("/simulations/{run_id}")
    def get_run(run_id: str, request: Request):
        return wire.run(read(run_id, request), store.count(run_id))

    @router.get("/simulations/{run_id}/result")
    def get_result(run_id: str, request: Request):
        row = terminal(read(run_id, request))
        return wire.result(row, wire.ordered_usage(store, row))

    @router.post("/simulations/{run_id}/cancel")
    def cancel(run_id: str, request: Request):
        row = read(run_id, request, True)
        store.cancel(run_id, row["owner"])
        return wire.run(read(run_id, request), store.count(run_id))

    def event_page(row, after):
        events = []
        size = 256
        for raw in store.events(row["id"], after):
            item = wire.event(row, raw)
            size += len(json.dumps(item).encode()) + 80
            if size > 240000:
                break
            events.append(item)
        cursor = events[-1]["sequence"] if events else after
        return wire.checked(
            "simulation-events",
            {
                "schema_version": "1.0",
                "run_id": row["id"],
                "events": events,
                "next_sequence": cursor,
                "terminal": row["status"] not in ("ACCEPTED", "RUNNING") and cursor == store.count(row["id"]),
            },
        )

    @router.get("/simulations/{run_id}/events")
    async def events(run_id: str, request: Request):
        row = read(run_id, request)
        query, header = request.query_params.get("after"), request.headers.get("last-event-id")
        values = [v for v in (query, header) if v is not None]
        if any(not re.fullmatch(r"[0-9]{1,4}", v) for v in values) or len({int(v) for v in values}) > 1:
            raise BoundaryError("INVALID_CURSOR")
        cursor = int(values[0]) if values else 0
        if cursor > store.count(run_id):
            raise BoundaryError("INVALID_CURSOR")
        if "text/event-stream" not in request.headers.get("accept", ""):
            return event_page(row, cursor)
        identity = row["owner"]
        streams = app.state.streams
        if streams.get(identity, 0) >= 4 or sum(streams.values()) >= 16:
            raise BoundaryError("RATE_LIMITED")
        streams[identity] = streams.get(identity, 0) + 1
        released = False

        def release():
            nonlocal released
            if not released:
                released = True
                streams[identity] -= 1
                if not streams[identity]:
                    del streams[identity]

        async def stream():
            nonlocal cursor
            size = 0
            try:
                for _ in range(120):
                    if await request.is_disconnected():
                        return
                    try:
                        current = read(run_id, request)
                    except (StoreError, BoundaryError):
                        return
                    page = event_page(current, cursor)
                    for item in page["events"]:
                        frame = f"id: {item['sequence']}\nevent: step\ndata: {json.dumps(item)}\n\n"
                        if size + len(frame.encode()) > 250000:
                            return
                        cursor = item["sequence"]
                        size += len(frame.encode())
                        yield frame
                    if page["terminal"]:
                        yield f'event: end\ndata: {{"last_sequence":{cursor}}}\n\n'
                        return
                    yield ": heartbeat\n\n"
                    size += 13
                    await asyncio.sleep(0.25)
            finally:
                release()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
            background=BackgroundTask(release),
        )

    @router.post("/inference")
    async def inference(request: Request):
        identity, credential = service(request, "inference:request")
        value = await body(request, "inference-request")
        if any(value[k] != credential.binding()[k] for k in ("application_id", "environment", "tenant_id")):
            raise BoundaryError("FORBIDDEN")
        if value["mode"] != wire.MODE:
            raise BoundaryError("AI_UNAVAILABLE")
        refusal = peer_profiles.refusal(value)
        if refusal is not None:
            # Refusals have no job or usage side effect, but remain subject to caller/global admission limits.
            with store.transaction() as con:
                now = time.time()
                store._limit(con, "runs-global", settings.global_runs_per_minute, now)
                store._limit(con, "runs:" + identity, settings.runs_per_minute, now)
            return JSONResponse(
                refusal, headers={"X-AgentOps-Capability-Profile": peer_profiles.FLOWOPS_REFUSAL}
            )
        if value["context_refs"] or value.get("extensions") or value["risk_class"] == "HIGH":
            raise BoundaryError("POLICY_BLOCKED")
        if value["currency"] != "USD" or any(
            c not in wire.CAPABILITIES.values() for c in value["required_capabilities"]
        ):
            raise BoundaryError("POLICY_BLOCKED")
        region_map = {v: k for k, v in wire.REGIONS.items()}
        if any(r not in region_map for r in value["allowed_regions"]):
            raise BoundaryError("POLICY_BLOCKED")
        caps = {v: k for k, v in wire.CAPABILITIES.items()}
        # Round the caller's cap down, never up, to the ledger's integer microdollars.
        inp = SimulationInput(
            task_type=value["task_type"],
            required_capabilities=[caps[c] for c in value["required_capabilities"]],
            data_classification=value["data_classification"],
            allowed_regions=[region_map[r] for r in value["allowed_regions"]],
            latency_deadline_ms=value["latency_deadline_ms"],
            input_tokens=value["max_input_tokens"],
            output_tokens=value["max_output_tokens"],
            available_funds_microusd=int(Decimal(str(value["max_cost"])) * 1000000),
            policy_version=POLICY_VERSION
            if value["routing_policy_version"] == POLICY_VERSION
            else "expired-policy",
            quality_floor=90 if value["risk_class"] == "MEDIUM" else 80,
        ).model_dump()
        env = envelope(
            inp, value["correlation_id"], value["operation_id"], value["trace_id"], credential.binding()
        )
        env["service_request"] = value
        run_id, _ = store.enqueue(identity, value["operation_id"], env, private=True)
        deadline = time.monotonic() + settings.max_run_seconds + 1
        while time.monotonic() < deadline:
            row = read(run_id, request)
            if row["status"] not in ("ACCEPTED", "RUNNING"):
                return JSONResponse(
                    wire.route(row, wire.ordered_usage(store, row)), headers={"X-AgentOps-Run-ID": run_id}
                )
            await asyncio.sleep(0.02)
        raise BoundaryError("SERVICE_UNAVAILABLE")

    @router.get("/simulations/{run_id}/route")
    def get_route(run_id: str, request: Request):
        row = terminal(read(run_id, request))
        return wire.route(row, wire.ordered_usage(store, row))

    @router.get("/simulations/{run_id}/usage/{attempt}")
    def get_usage(run_id: str, attempt: str, request: Request):
        service(request, "usage:read")
        row = read(run_id, request, scope="usage:read")
        records = wire.ordered_usage(store, row)
        if not re.fullmatch(r"[1-9][0-9]?", attempt) or int(attempt) > len(records):
            raise BoundaryError("NOT_FOUND")
        return wire.usage_record(row, records[int(attempt) - 1], int(attempt))

    @router.get("/openapi.json")
    def openapi():
        doc = json.loads((Path(__file__).parent / "vendor/ecosystem_contracts/openapi.json").read_text())
        doc["info"] = {"title": "AgentOps Contracts v1", "version": APP_VERSION}
        doc["servers"] = [{"url": PUBLIC_PREFIX}]
        doc["paths"] = {k: v for k, v in doc["paths"].items() if k not in ("/knowledge/query", "/events")}
        doc["paths"]["/inference/capabilities"] = {
            "get": {
                "operationId": "get_agentops_inference_capabilities",
                "security": [],
                "description": "App-owned admitted and refusal profiles; never an authorization grant.",
                "responses": {
                    "200": {
                        "description": "AgentOps peer profiles v1",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    }
                },
            }
        }
        errors = doc["paths"]["/inference"]["post"]["responses"]
        run_parameter = doc["paths"]["/simulations/{run_id}"]["get"]["parameters"]
        for path, resource in [
            ("/simulations/{run_id}/route", "route-decision"),
            ("/simulations/{run_id}/usage/{attempt}", "ai-usage-recorded"),
        ]:
            parameters = list(run_parameter)
            if resource == "ai-usage-recorded":
                parameters.append(
                    {
                        "name": "attempt",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer", "minimum": 1, "maximum": 16},
                    }
                )
            doc["paths"][path] = {
                "get": {
                    "parameters": parameters,
                    "security": [{"serviceBearer": []}],
                    "responses": {
                        **errors,
                        "200": {
                            "description": "Validated AgentOps response",
                            "content": {
                                "application/json": {"schema": {"$ref": f"#/components/schemas/{resource}"}}
                            },
                        },
                    },
                }
            }
        return doc

    @router.get("/inference/capabilities")
    def inference_capabilities():
        return peer_profiles.capabilities()

    @router.get("/portfolio-evidence")
    def portfolio_evidence():
        path = Path(__file__).parent / "adoption_evidence.json"
        if not path.is_file():
            raise BoundaryError("SERVICE_UNAVAILABLE")
        return wire.checked("portfolio-evidence", json.loads(path.read_text()))

    app.include_router(router)
    return app


def create_prefixed_app():
    """Private integration option for an edge that preserves the public prefix."""
    return create_app(prefix=PUBLIC_PREFIX)
