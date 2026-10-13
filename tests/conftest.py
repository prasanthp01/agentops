import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentops.app import PREFIX, create_app
from agentops.contracts import SimulationStart
from agentops.engine import Engine
from agentops.settings import Settings
from agentops.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(Settings(db_path=tmp_path / "test.db", workers=0, max_per_owner=100, runs_per_minute=1000))


@pytest.fixture
def run(store):
    def execute(**overrides):
        import secrets

        from agentops.contracts import SimulationInput

        body = SimulationStart(input=SimulationInput(**overrides)).model_dump()
        run_id, _ = store.enqueue("session:test", secrets.token_hex(12), body)
        job = store.claim()
        return Engine(store).execute(job), store.events(run_id)

    return execute


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(db_path=Path(tmp_path) / "api.db"))) as client:
        yield client


def session(client):
    response = client.post(PREFIX + "/session")
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def finish(client, run_id):
    for _ in range(200):
        response = client.get(PREFIX + f"/simulations/{run_id}/result")
        if response.status_code != 202:
            return response
        time.sleep(0.01)
    raise AssertionError("Run did not finish")
