"""Real AgentOps endpoints validated by the pinned canonical shape and semantic validator."""

import hashlib
import json
import secrets
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentops import canonical_wire as wire
from agentops.canonical_app import Config, Credential, create_app
from agentops.settings import Settings
from agentops.store import digest
from agentops.vendor.ecosystem_contracts import ContractError, validate_stream

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "/api/projects/agentops"
SOURCE = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def config(token, **changes):
    c = dict(
        application_id="flowops",
        environment="test",
        tenant_id="tenant-fixture",
        audience="agentops",
        token_sha256=digest(token),
        scopes=["inference:request", "usage:read"],
        budget_microusd=100000,
    )
    c.update(changes)
    return Config(credentials=[Credential(**c)], global_budget_microusd=200000)


@pytest.fixture
def api(tmp_path):
    token = secrets.token_urlsafe(32)
    app = create_app(
        Settings(db_path=tmp_path / "api.db", runs_per_minute=1000), config(token), SOURCE, PREFIX
    )
    with TestClient(app) as client:
        client.service_token = token
        yield client


def auth(client):
    r = client.post(PREFIX + "/simulation/session")
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def service(client):
    return {"Authorization": "Bearer " + client.service_token}


def start(client, headers, inp=None, **changes):
    value = dict(
        schema_version="1.0",
        project_id="agentops",
        mode=wire.MODE,
        seed=42,
        client_correlation_id="test-correlation",
        input=inp or {},
    )
    value.update(changes)
    r = client.post(PREFIX + "/simulations", json=value, headers=headers)
    assert r.status_code == 202, r.text
    wire.REGISTRY.validate("simulation-run", r.json())
    return r.json()["run_id"]


def finish(client, headers, run):
    for _ in range(250):
        r = client.get(PREFIX + f"/simulations/{run}/result", headers=headers)
        if r.status_code == 200:
            wire.REGISTRY.validate("simulation-result", r.json())
            return r.json()
        assert r.status_code == 409, r.text
        wire.REGISTRY.validate("error", r.json())
        time.sleep(0.01)
    raise AssertionError("No terminal result")


def inference(**changes):
    value = dict(
        schema_version="1.0",
        application_id="flowops",
        environment="test",
        tenant_id="tenant-fixture",
        task_type="summarize",
        risk_class="LOW",
        required_capabilities=["TEXT", "STRUCTURED_OUTPUT"],
        data_classification="INTERNAL",
        allowed_regions=["eu"],
        latency_deadline_ms=3000,
        max_input_tokens=2000,
        max_output_tokens=500,
        max_cost=0.02,
        currency="USD",
        routing_policy_version="policy-2026-10-v1",
        correlation_id="service-correlation",
        trace_id="service-trace",
        operation_id="op-" + secrets.token_hex(6),
        mode=wire.MODE,
        context_refs=[],
        extensions={},
    )
    value.update(changes)
    return value


def test_pin_and_format_validation():
    lock = json.loads((ROOT / "contracts.lock.json").read_text())
    assert lock["release_commit"] == "b55d1a9dc5d2bab23817b984b9801ccff7304f04"
    for path, checksum in lock["files"].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == checksum, path
    fixture = json.loads((ROOT / "tests/fixtures/contracts/valid/simulation-event.json").read_text())
    fixture["occurred_at"] = "2026-99-99T25:99:99Z"
    with pytest.raises(ContractError):
        wire.REGISTRY.validate("simulation-event", fixture)


@pytest.mark.parametrize(
    "path", sorted((ROOT / "tests/fixtures/contracts/valid").glob("*.json")), ids=lambda p: p.stem
)
def test_valid_canonical_fixture(path):
    wire.REGISTRY.validate(path.stem, json.loads(path.read_text()))


@pytest.mark.parametrize(
    "path", sorted((ROOT / "tests/fixtures/contracts/invalid").glob("*.json")), ids=lambda p: p.stem
)
def test_invalid_canonical_fixture(path):
    with pytest.raises(ContractError):
        wire.REGISTRY.validate(path.stem, json.loads(path.read_text()))


def test_metadata_and_all_nine_real_scenarios(api):
    s = api.get(PREFIX + "/simulation/schema").json()
    wire.REGISTRY.validate("simulation-schema", s)
    assert wire.sha(s["input_schema"]) == s["input_schema_sha256"]
    presets = api.get(PREFIX + "/simulation/presets").json()
    wire.REGISTRY.validate("simulation-presets", presets)
    headers = auth(api)
    outputs = {}
    for preset in presets["presets"]:
        run = start(api, headers, preset["input"], seed=preset["seed"], preset_id=preset["preset_id"])
        result = finish(api, headers, run)
        assert result["provenance"]["source_revision"] == SOURCE
        state = api.get(PREFIX + f"/simulations/{run}", headers=headers).json()
        wire.REGISTRY.validate("simulation-run", state)
        page = api.get(PREFIX + f"/simulations/{run}/events", headers=headers).json()
        wire.REGISTRY.validate("simulation-events", page)
        validate_stream(page["events"], run_id=run, project_id="agentops")
        assert state["last_sequence"] == len(page["events"]) == page["next_sequence"]
        route = api.get(PREFIX + f"/simulations/{run}/route", headers=headers).json()
        wire.REGISTRY.validate("route-decision", route)
        assert result["cost"] == route["usage"]["cost"]
        assert route["usage"]["provider_calls"] == 0
        outputs[preset["preset_id"]] = result["output"]
    assert outputs["economical"]["selected_model"] == "cedar-eu"
    assert outputs["outage"]["selected_model"] == "harbor-eu"
    assert outputs["outage"]["fallback_hops"] == 1
    assert outputs["budget"]["outcome"] == "BUDGET_EXHAUSTED"
    assert outputs["all-down"]["outcome"] == "AI_UNAVAILABLE"
    run = start(api, headers)
    repeat = finish(api, headers, run)
    assert repeat["output"] == outputs["economical"]


def test_cursor_replay_sse_and_ownership(api):
    headers = auth(api)
    run = start(api, headers, {"provider_failures": [{"model": "cedar-eu", "outcome": "503"}]})
    finish(api, headers, run)
    path = PREFIX + f"/simulations/{run}/events"
    page = api.get(path, headers=headers).json()
    total = page["next_sequence"]
    replay = api.get(path + "?after=2", headers=headers).json()
    validate_stream(replay["events"], run_id=run, project_id="agentops", after=2)
    sse = api.get(path, headers={**headers, "Accept": "text/event-stream", "Last-Event-ID": "2"})
    assert sse.status_code == 200
    assert "event: end" in sse.text and f'"last_sequence":{total}' in sse.text
    frames = [json.loads(b.split("data: ", 1)[1]) for b in sse.text.split("\n\n") if "event: step" in b]
    assert frames == replay["events"]
    assert len(sse.content) <= 262144
    for cursor in ["-1", "bad", "1.5", str(total + 1)]:
        r = api.get(path + "?after=" + cursor, headers=headers)
        assert r.status_code == 409
        wire.REGISTRY.validate("error", r.json())
        assert r.json()["code"] == "INVALID_CURSOR"
    assert api.get(path + "?after=2", headers={**headers, "Last-Event-ID": "3"}).status_code == 409
    assert api.get(path + f"?after={total}", headers=headers).json()["events"] == []
    other = auth(api)
    a = api.get(path, headers=other)
    b = api.get(PREFIX + "/simulations/missing/events", headers=other)
    assert a.status_code == b.status_code == 404 and a.json() == b.json()
    assert api.app.state.streams == {}


def test_pending_cancel_expiry_and_restart(tmp_path):
    settings = Settings(db_path=tmp_path / "persistence.db", workers=0)
    app = create_app(settings, source_revision=SOURCE, prefix=PREFIX)
    with TestClient(app) as c:
        h = auth(c)
        run = start(c, h)
        r = c.get(PREFIX + f"/simulations/{run}/result", headers=h)
        assert r.status_code == 409 and r.json()["code"] == "CONFLICT"
        for _ in range(2):
            r = c.post(PREFIX + f"/simulations/{run}/cancel", headers=h)
            assert r.json()["status"] == "CANCELLED"
        assert finish(c, h, run)["usage"]["kind"] == "NONE"
    with TestClient(create_app(settings, source_revision=SOURCE, prefix=PREFIX)) as c:
        assert c.get(PREFIX + f"/simulations/{run}", headers=h).json()["status"] == "CANCELLED"
        with c.app.state.store.transaction() as con:
            con.execute("UPDATE runs SET expires=? WHERE id=?", (time.time() - 1, run))
        assert c.get(PREFIX + f"/simulations/{run}/result", headers=h).status_code == 410
        other = auth(c)
        assert c.get(PREFIX + f"/simulations/{run}/result", headers=other).status_code == 404
        c.app.state.store.cleanup()
        assert c.get(PREFIX + f"/simulations/{run}", headers=h).status_code == 404


def test_cookie_csrf_and_input_errors(api):
    s = api.post(PREFIX + "/simulation/session?transport=cookie")
    assert "HttpOnly" in s.headers["set-cookie"] and "SameSite=strict" in s.headers["set-cookie"]
    csrf = s.json()["csrf_token"]
    assert api.post(PREFIX + "/simulations", json={}).status_code == 403
    r = api.post(
        PREFIX + "/simulations", json={}, headers={"X-CSRF-Token": csrf, "Origin": "https://evil.invalid"}
    )
    assert r.status_code == 403
    h = auth(api)
    for bad in [
        dict(input={"private_secret_property": "CANARY"}),
        dict(seed=65536),
        dict(input={"input_tokens": "3"}),
        dict(input={"provider_failures": [{"model": "cedar-eu", "outcome": "503"}] * 2}),
        dict(mode="LIVE_MODEL_AUTHENTICATED"),
    ]:
        payload = (
            dict(
                schema_version="1.0",
                project_id="agentops",
                mode=wire.MODE,
                seed=42,
                client_correlation_id="negative",
                input={},
            )
            | bad
        )
        r = api.post(PREFIX + "/simulations", json=payload, headers=h)
        assert r.status_code == 422
        assert "CANARY" not in r.text and "private_secret_property" not in r.text
        wire.REGISTRY.validate("error", r.json())
    for raw in ["{", "[" * 1000 + "0" + "]" * 1000, '{"x":"' + ("x" * 17000) + '"}']:
        r = api.post(PREFIX + "/simulations", content=raw, headers={**h, "Content-Type": "application/json"})
        assert r.status_code in (422, 413)
        wire.REGISTRY.validate("error", r.json())


def test_inference_usage_and_idempotent_settlement(api):
    h = service(api)
    body = inference()
    r = api.post(PREFIX + "/inference", json=body, headers=h)
    assert r.status_code == 200, r.text
    wire.REGISTRY.validate("route-decision", r.json())
    run = r.headers["X-AgentOps-Run-ID"]
    for _ in range(2):
        repeat = api.post(PREFIX + "/inference", json=body, headers=h)
        assert repeat.headers["X-AgentOps-Run-ID"] == run
        assert repeat.json() == r.json()
    changed = body | {"max_output_tokens": 501}
    assert api.post(PREFIX + "/inference", json=changed, headers=h).status_code == 409
    record = api.get(PREFIX + f"/simulations/{run}/usage/1", headers=h)
    assert record.status_code == 200, record.text
    wire.REGISTRY.validate("ai-usage-recorded", record.json())
    assert record.json()["usage"]["kind"] == "SIMULATED"
    assert record.json()["usage"]["provider_calls"] == 0
    assert record.json()["usage"]["cost"] == r.json()["usage"]["cost"]
    assert api.get(PREFIX + f"/simulations/{run}/usage/2", headers=h).status_code == 404
    assert api.get(PREFIX + f"/simulations/{run}/usage/1", headers=auth(api)).status_code == 401
    with api.app.state.store.connection() as con:
        assert con.execute("SELECT used FROM budgets WHERE account='app:flowops'").fetchone()[0] == 280


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"application_id": "resolveai"}, 403),
        ({"tenant_id": "foreign"}, 403),
        ({"environment": "production"}, 403),
        ({"mode": "LIVE_MODEL_AUTHENTICATED"}, 503),
        ({"risk_class": "HIGH"}, 403),
        ({"context_refs": ["unauthorized-source"]}, 403),
        ({"required_capabilities": ["AUDIO"]}, 403),
        ({"allowed_regions": ["eu-private"]}, 403),
        ({"currency": "EUR"}, 403),
        ({"max_cost": 2}, 422),
    ],
)
def test_service_guards(api, changes, status):
    r = api.post(PREFIX + "/inference", json=inference(**changes), headers=service(api))
    assert r.status_code == status, r.text
    wire.REGISTRY.validate("error", r.json())
    assert api.app.state.store.ledger("app:flowops")["usage"] == []


def test_private_fallback_failed_attempt_accounting_and_budget(api, monkeypatch):
    from agentops import engine

    original = engine.invoke_mock

    def outage(model, request, attempt):
        if model.id == "cedar-eu":
            return "invalid_json", {"synthetic": "bad"}
        return original(model, request, attempt)

    monkeypatch.setattr(engine, "invoke_mock", outage)
    r = api.post(PREFIX + "/inference", json=inference(), headers=service(api))
    assert r.status_code == 200, r.text
    decision = r.json()
    wire.REGISTRY.validate("route-decision", decision)
    assert decision["selected_deployment"]["deployment_id"] == "harbor-eu-fixture"
    assert decision["fallback_kind"] == "MODEL"
    assert [a["outcome"] for a in decision["attempts"]] == ["SCHEMA_INVALID", "SUCCEEDED"]
    assert decision["usage"]["cost"]["value"] == 0.00094
    for body, outcome in [
        (inference(max_cost=0), "BUDGET_EXCEEDED"),
        (inference(routing_policy_version="obsolete"), "POLICY_BLOCKED"),
    ]:
        r = api.post(PREFIX + "/inference", json=body, headers=service(api))
        assert r.status_code == 200, r.text
        wire.REGISTRY.validate("route-decision", r.json())
        assert r.json()["outcome"] == outcome and r.json()["attempts"] == []


def test_rate_limit_storage_failure_and_scope(tmp_path, monkeypatch):
    token = secrets.token_urlsafe(32)
    with TestClient(
        create_app(
            Settings(db_path=tmp_path / "limits.db", workers=0, runs_per_minute=1),
            config(token, scopes=["usage:read"]),
            SOURCE,
            PREFIX,
        )
    ) as c:
        h = auth(c)
        start(c, h)
        r = c.post(
            PREFIX + "/simulations",
            headers=h,
            json=dict(
                schema_version="1.0",
                project_id="agentops",
                mode=wire.MODE,
                seed=42,
                client_correlation_id="second",
                input={},
            ),
        )
        assert r.status_code == 429 and r.headers["Retry-After"] == "60"
        wire.REGISTRY.validate("error", r.json())
        assert (
            c.post(
                PREFIX + "/inference", json=inference(), headers={"Authorization": "Bearer " + token}
            ).status_code
            == 403
        )

        def unavailable(*args, **kwargs):
            raise sqlite3.OperationalError("PRIVATE_DIAGNOSTIC")

        monkeypatch.setattr(c.app.state.store, "count", unavailable)
        # Storage failure must not echo exception text.
        with c.app.state.store.connection() as con:
            run = con.execute("SELECT id FROM runs").fetchone()[0]
        r = c.get(PREFIX + f"/simulations/{run}", headers=h)
        assert r.status_code == 503 and "PRIVATE_DIAGNOSTIC" not in r.text
        wire.REGISTRY.validate("error", r.json())


def test_rebinding_cannot_read_previous_tenant_even_expired(tmp_path):
    token = secrets.token_urlsafe(32)
    settings = Settings(db_path=tmp_path / "tenant.db")
    with TestClient(create_app(settings, config(token), SOURCE, PREFIX)) as c:
        h = {"Authorization": "Bearer " + token}
        r = c.post(PREFIX + "/inference", json=inference(), headers=h)
        run = r.headers["X-AgentOps-Run-ID"]
    with TestClient(create_app(settings, config(token, tenant_id="different"), SOURCE, PREFIX)) as c:
        assert c.get(PREFIX + f"/simulations/{run}", headers=h).status_code == 404
        with c.app.state.store.transaction() as con:
            con.execute("UPDATE runs SET expires=? WHERE id=?", (time.time() - 1, run))
        assert c.get(PREFIX + f"/simulations/{run}", headers=h).status_code == 404


def test_actual_accounting_is_never_relabelled_as_simulated():
    with pytest.raises(RuntimeError, match="actual provider spend"):
        wire.usage(
            "private-record", [{"kind": "ACTUAL", "currency": "USD", "external_llm_spend_microusd": 1}]
        )


def test_global_budget_is_atomic_for_simultaneous_private_requests(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    token = secrets.token_urlsafe(32)
    cfg = config(token)
    cfg.global_budget_microusd = 500
    with TestClient(create_app(Settings(db_path=tmp_path / "budget.db"), cfg, SOURCE, PREFIX)) as c:
        h = {"Authorization": "Bearer " + token}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(lambda _: c.post(PREFIX + "/inference", json=inference(), headers=h), range(2))
            )
        assert all(r.status_code == 200 for r in results)
        assert sorted(r.json()["outcome"] for r in results) == ["BUDGET_EXCEEDED", "SUCCEEDED"]
        with c.app.state.store.connection() as con:
            budget = con.execute("SELECT used,reserved FROM budgets WHERE account='global'").fetchone()
            assert tuple(budget) == (280, 0)


def test_session_expiry_and_secure_cookie(tmp_path):
    with TestClient(
        create_app(
            Settings(db_path=tmp_path / "cookie.db", secure_cookies=True),
            source_revision=SOURCE,
            prefix=PREFIX,
        ),
        base_url="https://testserver",
    ) as c:
        r = c.post(PREFIX + "/simulation/session?transport=cookie")
        assert "; Secure" in r.headers["set-cookie"]
        h = {"X-CSRF-Token": r.json()["csrf_token"]}
        run = start(c, h)
        assert finish(c, h, run)["status"] == "SUCCEEDED"
        with c.app.state.store.transaction() as con:
            con.execute("UPDATE sessions SET expires=0")
        assert c.get(PREFIX + f"/simulations/{run}", headers=h).status_code == 401
        assert (
            c.post(PREFIX + "/simulation/session", headers={"Origin": "https://foreign.invalid"}).status_code
            == 403
        )


def test_pagination_is_bounded_and_contiguous(tmp_path):
    with TestClient(
        create_app(Settings(db_path=tmp_path / "pages.db", workers=0), source_revision=SOURCE, prefix=PREFIX)
    ) as c:
        h = auth(c)
        run = start(c, h)
        store = c.app.state.store
        job = store.claim()
        for i in range(301):
            store.emit(
                run,
                job["claim"],
                f"test:{i}",
                {
                    "schema_version": "1.0",
                    "run_id": run,
                    "project_id": "agentops",
                    "step_id": f"test:{i}",
                    "step_type": "validate",
                    "component": "test",
                    "status": "SUCCEEDED",
                    "mode": wire.MODE,
                    "title": "Bounded pagination fixture",
                    "overview": "Synthetic log replay test.",
                    "technical": {},
                    "trace_id": run,
                    "duration_ms": 0,
                    "usage": {"kind": "NONE"},
                },
            )
        c.post(PREFIX + f"/simulations/{run}/cancel", headers=h)
        after = 0
        pages = 0
        while after < 301:
            r = c.get(PREFIX + f"/simulations/{run}/events?after={after}", headers=h)
            assert r.status_code == 200 and len(r.content) <= 262144
            page = r.json()
            validate_stream(page["events"], run_id=run, project_id="agentops", after=after)
            assert page["next_sequence"] > after
            after = page["next_sequence"]
            pages += 1
            assert page["terminal"] == (after == 301)
        assert pages == 4
        # A long SSE connection closes before the byte cap; cursor replay retrieves the remainder.
        r = c.get(PREFIX + f"/simulations/{run}/events", headers={**h, "Accept": "text/event-stream"})
        assert r.status_code == 200 and len(r.content) <= 262144
        assert "event: end" not in r.text
        frames = [json.loads(b.split("data: ", 1)[1]) for b in r.text.split("\n\n") if "event: step" in b]
        validate_stream(frames, run_id=run, project_id="agentops")
        assert 0 < len(frames) < 301


def test_advertised_openapi_matches_agentops_surface(api):
    document = api.get(PREFIX + "/openapi.json")
    assert document.status_code == 200
    spec = document.json()
    assert "/inference" in spec["paths"]
    assert "/simulations/{run_id}/usage/{attempt}" in spec["paths"]
    assert "/events" not in spec["paths"] and "/knowledge/query" not in spec["paths"]
    assert spec["servers"] == [{"url": PREFIX}]
