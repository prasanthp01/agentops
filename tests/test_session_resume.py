import secrets
import time

from agentops.app import COOKIE, PREFIX
from agentops.store import digest
from tests.conftest import finish, session


def test_second_tab_and_reload_keep_the_first_tabs_runs_accessible(client):
    first = session(client)
    original_cookie = client.cookies.get(COOKIE)
    run_id = client.post(PREFIX + "/simulations", json={"input": {}}, headers=first).json()["run_id"]
    finish(client, run_id)
    resumed = client.post(PREFIX + "/session")
    assert resumed.status_code == 200
    assert resumed.json()["csrf_token"] == first["X-CSRF-Token"]
    assert "set-cookie" not in resumed.headers
    assert client.cookies.get(COOKIE) == original_cookie
    assert client.get(PREFIX + f"/simulations/{run_id}/result").status_code == 200
    assert client.post(PREFIX + "/simulations", json={"input": {}}, headers=first).status_code == 202
    with client.app.state.store.connection() as con:
        assert con.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
        assert con.execute("SELECT count FROM limits WHERE key='session-global'").fetchone()[0] == 1


def test_resume_does_not_extend_expiry(client):
    session(client)
    expiry = time.time() + 60
    with client.app.state.store.connection() as con:
        con.execute("UPDATE sessions SET expires=?", (expiry,))
    resumed = client.post(PREFIX + "/session")
    assert 0 < resumed.json()["expires_in_seconds"] <= 60
    with client.app.state.store.connection() as con:
        assert con.execute("SELECT expires FROM sessions").fetchone()[0] == expiry


def test_expired_session_replaced_without_exposing_old_runs(client):
    first = session(client)
    old_cookie = client.cookies.get(COOKIE)
    run_id = client.post(PREFIX + "/simulations", json={"input": {}}, headers=first).json()["run_id"]
    finish(client, run_id)
    with client.app.state.store.connection() as con:
        con.execute("UPDATE sessions SET expires=0")
    replacement = client.post(PREFIX + "/session")
    assert replacement.status_code == 200
    assert client.cookies.get(COOKIE) != old_cookie
    assert client.get(PREFIX + f"/simulations/{run_id}/result").status_code == 404
    assert client.post(PREFIX + "/simulations", json={"input": {}}, headers=first).status_code == 403


def test_legacy_tab_csrf_remains_valid_after_session_resume(client):
    session(client)
    cookie = client.cookies.get(COOKIE)
    legacy_csrf = secrets.token_urlsafe(32)
    with client.app.state.store.connection() as con:
        con.execute("UPDATE sessions SET csrf_hash=?", (digest(legacy_csrf),))
    fresh = session(client)
    assert client.cookies.get(COOKIE) == cookie
    assert fresh["X-CSRF-Token"] != legacy_csrf
    for csrf in [legacy_csrf, fresh["X-CSRF-Token"]]:
        response = client.post(PREFIX + "/simulations", json={"input": {}}, headers={"X-CSRF-Token": csrf})
        assert response.status_code == 202
    assert (
        client.post(
            PREFIX + "/simulations", json={"input": {}}, headers={"X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )
    assert client.post(PREFIX + "/session", headers={"Origin": "https://invalid.example"}).status_code == 403
