import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from agentops.contracts import PrivateConfig, ServiceCredential, SimulationInput, SimulationStart
from agentops.engine import Engine
from agentops.settings import Settings
from agentops.store import LeaseLost, Store, StoreError, digest


def enqueue(store, owner="session:a", operation="op1", funds=1000):
    return store.enqueue(
        owner, operation, SimulationStart(input=SimulationInput(available_funds_microusd=funds)).model_dump()
    )[0]


def test_idempotency_scoped_and_payload_bound(store):
    first = enqueue(store)
    assert enqueue(store) == first
    assert enqueue(store, "session:b") != first
    with pytest.raises(StoreError, match="IDEMPOTENCY_CONFLICT"):
        enqueue(store, funds=2000)


def test_concurrent_reservations_never_oversubscribe(store):
    run_id = enqueue(store, funds=1000)
    job = store.claim()

    def reserve(i):
        try:
            store.reserve(run_id, job["claim"], str(i), 100)
            return True
        except StoreError:
            return False

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(reserve, range(40)))
    assert sum(results) == 10
    with store.connection() as con:
        assert (
            con.execute("SELECT reserved FROM budgets WHERE account=?", ("run:" + run_id,)).fetchone()[0]
            == 1000
        )


def test_shared_app_and_global_budget(tmp_path):
    cred = ServiceCredential(
        application_id="flowops",
        token_sha256=digest("test"),
        environments=["development"],
        scopes=["inference:write"],
        budget_microusd=500,
    )
    store = Store(
        Settings(
            db_path=tmp_path / "private.db",
            private=PrivateConfig(credentials=[cred], global_budget_microusd=300),
        )
    )
    a = enqueue(store, "app:flowops", "a")
    b = enqueue(store, "app:flowops", "b")
    ja, jb = store.claim(), store.claim()
    store.reserve(a, ja["claim"], "try", 200)
    with pytest.raises(StoreError, match="BUDGET_EXHAUSTED"):
        store.reserve(b, jb["claim"], "try", 200)
    store.settle(a, ja["claim"], "try", 100, {"usage_id": "a", "cost_microusd": 100})
    store.reserve(b, jb["claim"], "try", 200)


def test_usage_dedup_and_conflict(store):
    run_id = enqueue(store)
    job = store.claim()
    store.reserve(run_id, job["claim"], "one", 500)
    payload = {"usage_id": "one", "cost_microusd": 300}
    store.settle(run_id, job["claim"], "one", 300, payload)
    store.settle(run_id, job["claim"], "one", 300, payload)
    assert store.usage(run_id) == [payload]
    with pytest.raises(StoreError, match="USAGE_CONFLICT"):
        store.settle(run_id, job["claim"], "one", 301, {"cost_microusd": 301})
    with store.connection() as con:
        assert tuple(
            con.execute("SELECT used,reserved FROM budgets WHERE account=?", ("run:" + run_id,)).fetchone()
        ) == (300, 0)


def test_stale_worker_is_fenced_after_reclaim(store):
    run_id = enqueue(store)
    old = store.claim()
    with store.transaction() as con:
        con.execute("UPDATE runs SET lease=? WHERE id=?", (time.time() - 1, run_id))
    new = store.claim()
    assert new["claim"] > old["claim"]
    for action in [
        lambda: store.reserve(run_id, old["claim"], "a", 100),
        lambda: store.emit(run_id, old["claim"], "x", {}),
        lambda: store.finish(run_id, old["claim"], {"status": "SUCCEEDED"}),
        lambda: store.heartbeat(run_id, old["claim"]),
    ]:
        with pytest.raises(LeaseLost):
            action()
    assert Engine(store).execute(new)["status"] == "SUCCEEDED"


def test_recovery_after_settlement_before_result(store):
    run_id = enqueue(store)
    old = store.claim()
    original_finish = store.finish
    store.finish = lambda *a: (_ for _ in ()).throw(RuntimeError("simulated crash"))
    with pytest.raises(RuntimeError):
        Engine(store).execute(old)
    store.finish = original_finish
    assert len(store.usage(run_id)) == 1
    before = store.events(run_id)
    with store.transaction() as con:
        con.execute("UPDATE runs SET lease=0 WHERE id=?", (run_id,))
    result = Engine(store).execute(store.claim())
    assert result["status"] == "SUCCEEDED"
    assert len(store.usage(run_id)) == 1
    assert store.events(run_id) == before


def test_cancel_releases_reservation_and_fences_worker(store):
    run_id = enqueue(store)
    job = store.claim()
    store.reserve(run_id, job["claim"], "a", 500)
    store.cancel(run_id, "session:a")
    with pytest.raises(LeaseLost):
        store.settle(run_id, job["claim"], "a", 100, {})
    assert store.get(run_id, "session:a")["status"] == "CANCELLED"
    with store.connection() as con:
        assert (
            con.execute("SELECT reserved FROM budgets WHERE account=?", ("run:" + run_id,)).fetchone()[0] == 0
        )


def test_backup_restore_and_cleanup(store, tmp_path):
    run_id = enqueue(store)
    Engine(store).execute(store.claim())
    target = tmp_path / "backup.db"
    store.backup(target)
    restored = Store(Settings(db_path=target, workers=0))
    assert json.loads(restored.get(run_id, "session:a")["result"])["status"] == "SUCCEEDED"
    assert restored.usage(run_id) == store.usage(run_id)
    with store.transaction() as con:
        con.execute("UPDATE runs SET expires=0")
    store.cleanup()
    with pytest.raises(StoreError):
        store.get(run_id, "session:a")
    with store.connection() as con:
        assert con.execute("SELECT count(*) FROM attempts").fetchone()[0] == 0


def test_per_owner_and_global_concurrency(tmp_path):
    store = Store(Settings(db_path=tmp_path / "cap.db", workers=0, max_per_owner=1, max_active=2))
    enqueue(store)
    with pytest.raises(StoreError, match="CONCURRENCY_LIMIT"):
        enqueue(store, operation="op2")
    enqueue(store, owner="session:b")
    with pytest.raises(StoreError, match="CONCURRENCY_LIMIT"):
        enqueue(store, owner="session:c")


def test_settlement_amount_must_match_payload(store):
    run_id = enqueue(store)
    job = store.claim()
    store.reserve(run_id, job["claim"], "one", 500)
    with pytest.raises(StoreError, match="INVALID_SETTLEMENT"):
        store.settle(run_id, job["claim"], "one", 300, {"cost_microusd": 100})
    with pytest.raises(StoreError, match="INVALID_RESERVATION"):
        store.reserve(run_id, job["claim"], "negative", -100)


def test_retention_capacity_is_bounded(tmp_path):
    store = Store(Settings(db_path=tmp_path / "retained.db", workers=0, max_retained_runs=1))
    enqueue(store)
    Engine(store).execute(store.claim())
    with pytest.raises(StoreError, match="RETENTION_CAPACITY"):
        enqueue(store, operation="second")


def test_actual_process_exit_preserves_reservation_and_recovers(store):
    import subprocess
    import sys

    run_id = enqueue(store)
    script = """
import os, sys
from pathlib import Path
from agentops.settings import Settings
from agentops.store import Store
store = Store(Settings(db_path=Path(sys.argv[1]), workers=0, lease_seconds=1))
job = store.claim()
store.reserve(job['id'], job['claim'], 'cedar-eu:0', 280)
os._exit(23)
"""
    child = subprocess.run([sys.executable, "-c", script, str(store.path)], capture_output=True, timeout=5)
    assert child.returncode == 23
    with store.connection() as con:
        assert (
            con.execute("SELECT reserved FROM budgets WHERE account=?", ("run:" + run_id,)).fetchone()[0]
            == 280
        )
    time.sleep(1.1)
    recovered = store.claim()
    assert recovered["claim"] == 2
    result = Engine(store).execute(recovered)
    assert result["status"] == "SUCCEEDED"
    assert len(store.usage(run_id)) == 1
    with store.connection() as con:
        assert tuple(
            con.execute("SELECT used,reserved FROM budgets WHERE account=?", ("run:" + run_id,)).fetchone()
        ) == (280, 0)
