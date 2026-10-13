import asyncio
import json
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .api_types import failure_result
from .catalog import catalog, presets
from .contracts import InferenceRequest, RunState, SimulationInput, SimulationStart
from .settings import Settings
from .store import Store, StoreError, digest, utc
from .worker import Workers

PREFIX = "/api/projects/agentops"
WEB = Path(__file__).parent / "static"
COOKIE = "agentops_session"


class BodyLimit:
    def __init__(self, app, limit=16384):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.limit:
                return await JSONResponse({"error": {"code": "PAYLOAD_TOO_LARGE"}}, 413)(scope, receive, send)
            if not message.get("more_body"):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)


def create_app(settings=None):
    settings = settings or Settings.from_env()
    store = Store(settings)
    workers = Workers(store)

    @asynccontextmanager
    async def lifespan(app):
        workers.start()
        yield
        workers.close()

    app = FastAPI(
        title="AgentOps",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=PREFIX + "/openapi.json",
    )
    app.state.store, app.state.settings = store, settings
    app.state.streams = {}
    app.add_middleware(BodyLimit)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.allowed_hosts,
    )

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        if request.url.path.startswith(PREFIX):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path == "/projects/agentops" or request.url.path.startswith("/projects/agentops/"):
            # Unversioned public files must revalidate across deployments.
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.exception_handler(StoreError)
    async def store_error(request, exc):
        return JSONResponse(
            {"error": {"code": exc.code}},
            exc.status,
            headers={"Retry-After": "60"} if exc.status == 429 else None,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Error locations may themselves be attacker-controlled dictionary keys.
        return JSONResponse(
            {
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "Input does not match the published bounded schema.",
                }
            },
            422,
        )

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request, exc):
        return JSONResponse({"error": {"code": "STORAGE_UNAVAILABLE"}}, 503)

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        return JSONResponse({"error": {"code": "INTERNAL_ERROR"}}, 500)

    def origin_check(request):
        origin = request.headers.get("origin")
        expected = settings.public_origin or (
            ("https" if settings.secure_cookies else request.url.scheme) + "://" + request.headers["host"]
        )
        if (origin and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
            raise StoreError("ORIGIN_REJECTED", 403)

    def session_owner(request, mutate=False):
        if mutate:
            origin_check(request)
        csrf = request.headers.get("x-csrf-token", "") if mutate else None
        return store.authenticate_session(request.cookies.get(COOKIE), csrf)

    def service_owner(request, scope):
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or len(auth) > 300:
            raise StoreError("SERVICE_AUTH_REQUIRED", 401)
        token_hash = digest(auth[7:])
        for credential in settings.private.credentials:
            if secrets.compare_digest(token_hash, credential.token_sha256):
                if scope not in credential.scopes:
                    raise StoreError("SCOPE_FORBIDDEN", 403)
                return "app:" + credential.application_id, credential
        raise StoreError("SERVICE_AUTH_REQUIRED", 401)

    def owner_for_read(request):
        if request.headers.get("authorization"):
            return service_owner(request, "inference:write")[0]
        return session_owner(request)

    def state(row):
        return RunState(
            run_id=row["id"],
            mode=row["mode"],
            status=row["status"],
            created_at=utc(row["created"]),
            completed_at=utc(row["completed"]) if row["completed"] else None,
            event_count=len(store.events(row["id"])),
            result_reference=f"{PREFIX}/simulations/{row['id']}/result",
        )

    @app.get("/healthz")
    @app.get(PREFIX + "/healthz")
    def health():
        with store.connection() as con:
            con.execute("SELECT 1")
        return {
            "status": "ok",
            "version": __version__,
            "mode": "SANDBOX_DETERMINISTIC",
            "live_inference_enabled": False,
        }

    @app.post(PREFIX + "/session")
    def session(request: Request, response: Response):
        origin_check(request)
        try:
            csrf, remaining = store.resume_session(request.cookies.get(COOKIE))
            return {"csrf_token": csrf, "expires_in_seconds": remaining}
        except StoreError as error:
            if error.status != 401:
                raise
        token, csrf = store.session(request.client.host if request.client else "unknown")
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            secure=settings.secure_cookies,
            samesite="strict",
            path=PREFIX,
            max_age=settings.session_ttl_seconds,
        )
        return {"csrf_token": csrf, "expires_in_seconds": settings.session_ttl_seconds}

    @app.get(PREFIX + "/simulation/schema")
    def schema():
        return {
            "schema_version": "1.0",
            "project_id": "agentops",
            "input_schema": SimulationInput.model_json_schema(),
            "start_schema": SimulationStart.model_json_schema(),
            "supported_modes": ["SANDBOX_DETERMINISTIC"],
        }

    @app.get(PREFIX + "/simulation/presets")
    def get_presets():
        return {"presets": presets()}

    @app.get(PREFIX + "/catalog")
    def get_catalog():
        return {"kind": "SYNTHETIC", "models": [model.model_dump() for model in catalog()]}

    @app.post(PREFIX + "/simulations", status_code=202, response_model=RunState)
    def start_run(body: SimulationStart, request: Request):
        owner = session_owner(request, True)
        if body.preset_id and body.preset_id not in {preset["id"] for preset in presets()}:
            raise StoreError("UNKNOWN_PRESET", 422)
        key = request.headers.get("idempotency-key") or secrets.token_urlsafe(24)
        if not 1 <= len(key) <= 100 or not key.isascii():
            raise StoreError("INVALID_IDEMPOTENCY_KEY", 422)
        run_id, _ = store.enqueue(owner, key, body.model_dump())
        return state(store.get(run_id, owner))

    @app.get(PREFIX + "/simulations/{run_id}", response_model=RunState)
    def get_run(run_id: str, request: Request):
        return state(store.get(run_id, owner_for_read(request)))

    @app.get(PREFIX + "/simulations/{run_id}/result")
    def get_result(run_id: str, request: Request):
        row = store.get(run_id, owner_for_read(request))
        if row["result"]:
            return json.loads(row["result"])
        if row["status"] == "CANCELLED":
            result = failure_result(run_id)
            usage = store.usage(run_id)
            result["output"]["usage"] = usage
            result["cost"] = {
                "kind": "SIMULATED",
                "value": sum(u["cost_microusd"] for u in usage) / 1000000,
                "currency": "USD",
            }
            return result
        return JSONResponse({"status": row["status"]}, 202)

    @app.post(PREFIX + "/simulations/{run_id}/cancel", response_model=RunState)
    def cancel(run_id: str, request: Request):
        owner = (
            service_owner(request, "inference:write")[0]
            if request.headers.get("authorization")
            else session_owner(request, True)
        )
        store.cancel(run_id, owner)
        return state(store.get(run_id, owner))

    @app.get(PREFIX + "/simulations/{run_id}/events")
    async def events(run_id: str, request: Request, after: int = 0, transport: str = "sse"):
        owner = owner_for_read(request)
        store.get(run_id, owner)
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise HTTPException(422, "Invalid event cursor") from None
        if cursor < 0 or cursor > 1000 or transport not in ("sse", "poll"):
            raise HTTPException(422, "Invalid event query")
        if transport == "poll":
            return {"events": store.events(run_id, cursor)}

        streams = app.state.streams
        if streams.get(owner, 0) >= 4 or sum(streams.values()) >= 64:
            raise StoreError("STREAM_LIMIT", 429)
        streams[owner] = streams.get(owner, 0) + 1
        released = False

        def release_stream():
            nonlocal released
            if not released:
                released = True
                streams[owner] -= 1
                if not streams[owner]:
                    del streams[owner]

        async def stream():
            nonlocal cursor
            try:
                # Bounded connection, re-authorize every heartbeat; no arbitrary stream lifetime.
                for _ in range(120):
                    if await request.is_disconnected():
                        return
                    try:
                        owner_for_read(request)
                        row = store.get(run_id, owner)
                    except StoreError:
                        return
                    for event in store.events(run_id, cursor):
                        cursor = event["sequence"]
                        yield f"id: {cursor}\nevent: step\ndata: {json.dumps(event)}\n\n"
                    if row["status"] not in ("ACCEPTED", "RUNNING"):
                        yield "event: done\ndata: {}\n\n"
                        return
                    yield ": heartbeat\n\n"
                    await asyncio.sleep(0.25)
            finally:
                release_stream()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
            background=BackgroundTask(release_stream),
        )

    @app.post(PREFIX + "/inferences", status_code=202, response_model=RunState)
    def inference(body: InferenceRequest, request: Request):
        owner, credential = service_owner(request, "inference:write")
        if (
            body.application_id != credential.application_id
            or body.environment not in credential.environments
        ):
            raise StoreError("CALLER_SCOPE_MISMATCH", 403)
        if body.mode != "SANDBOX_DETERMINISTIC":
            raise StoreError("LIVE_MODEL_NOT_CONFIGURED", 503)
        envelope = SimulationStart(input=body.simulation()).model_dump()
        # Include immutable service metadata in the request digest and private record.
        envelope["service_request"] = body.model_dump()
        run_id, _ = store.enqueue(owner, body.operation_id, envelope, private=True)
        return state(store.get(run_id, owner))

    @app.get(PREFIX + "/usage")
    def usage(request: Request):
        owner, _ = service_owner(request, "usage:read")
        return store.ledger(owner)

    @app.get(PREFIX + "/policy")
    def policy(request: Request):
        service_owner(request, "policy:read")
        return {
            "schema_version": "1.1",
            "policy_version": "policy-2026-10-v1",
            "direct_provider_on_outage": False,
            "fallback": "LOCAL_DETERMINISTIC_OR_HUMAN",
            "live_inference_enabled": False,
            "catalog_kind": "SYNTHETIC",
        }

    @app.get("/")
    def local_root():
        return RedirectResponse("/projects/agentops", status_code=307)

    @app.get("/projects/agentops")
    @app.get("/projects/agentops/")
    def index():
        return FileResponse(WEB / "index.html")

    app.mount("/projects/agentops/assets", StaticFiles(directory=WEB), name="assets")
    return app
