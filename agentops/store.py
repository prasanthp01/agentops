"""Single-host SQLite ledger and queue. All mutations use short serial transactions."""

import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .settings import Settings


def utc(ts=None):
    return datetime.fromtimestamp(time.time() if ts is None else ts, UTC).isoformat()


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


def session_csrf(token: str):
    # Domain separation keeps this distinct from the stored session-token hash.
    return digest("agentops:csrf:v1:" + token)


class StoreError(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status


class LeaseLost(StoreError):
    def __init__(self):
        super().__init__("LEASE_LOST")


DDL = """
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO metadata VALUES ('schema_version','1');
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, csrf_hash TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS limits(key TEXT NOT NULL, bucket INTEGER NOT NULL, count INTEGER NOT NULL, PRIMARY KEY(key,bucket));
CREATE TABLE IF NOT EXISTS runs(
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, operation_id TEXT NOT NULL, request_hash TEXT NOT NULL,
 request TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ACCEPTED', created REAL NOT NULL,
 completed REAL, expires REAL NOT NULL, claim INTEGER NOT NULL DEFAULT 0, lease REAL NOT NULL DEFAULT 0,
 result TEXT, UNIQUE(owner,operation_id));
CREATE INDEX IF NOT EXISTS runs_queue ON runs(status,lease,created);
CREATE INDEX IF NOT EXISTS runs_owner ON runs(owner,created);
CREATE TABLE IF NOT EXISTS events(
 run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, event_key TEXT NOT NULL,
 sequence INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(run_id,event_key), UNIQUE(run_id,sequence));
CREATE TABLE IF NOT EXISTS budgets(account TEXT PRIMARY KEY, cap INTEGER NOT NULL CHECK(cap>=0),
 used INTEGER NOT NULL DEFAULT 0 CHECK(used>=0), reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved>=0),
 CHECK(used+reserved<=cap));
CREATE TABLE IF NOT EXISTS attempts(
 run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, attempt_id TEXT NOT NULL,
 amount INTEGER NOT NULL, state TEXT NOT NULL, accounts TEXT NOT NULL, payload TEXT,
 PRIMARY KEY(run_id,attempt_id));
"""


class Store:
    def __init__(self, settings: Settings):
        self.settings, self.path = settings, Path(settings.db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(DDL)
            if con.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0] != "1":
                raise RuntimeError("Unsupported database version")
        self.path.chmod(0o600)
        with self.transaction() as con:
            self._account(con, "global", settings.private.global_budget_microusd)
            for credential in settings.private.credentials:
                self._account(con, "app:" + credential.application_id, credential.budget_microusd)

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=10000")
        try:
            yield con
        finally:
            con.close()

    @contextmanager
    def transaction(self):
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                yield con
                con.commit()
            except BaseException:
                con.rollback()
                raise

    @staticmethod
    def _account(con, account, cap):
        row = con.execute("SELECT * FROM budgets WHERE account=?", (account,)).fetchone()
        if row and row["used"] + row["reserved"] > cap:
            raise RuntimeError("Configured budget below committed usage")
        con.execute(
            "INSERT INTO budgets(account,cap) VALUES (?,?) ON CONFLICT(account) DO UPDATE SET cap=excluded.cap",
            (account, cap),
        )

    @staticmethod
    def _limit(con, key, maximum, now):
        bucket = int(now // 60)
        row = con.execute("SELECT count FROM limits WHERE key=? AND bucket=?", (key, bucket)).fetchone()
        if row and row[0] >= maximum:
            raise StoreError("RATE_LIMITED", 429)
        con.execute(
            "INSERT INTO limits VALUES(?,?,1) ON CONFLICT(key,bucket) DO UPDATE SET count=count+1",
            (key, bucket),
        )

    def session(self, client_key, *, token_prefix=""):
        token, now = token_prefix + secrets.token_urlsafe(32), time.time()
        csrf = session_csrf(token)
        with self.transaction() as con:
            self._limit(con, "session-global", 200, now)
            self._limit(con, "session:" + digest(client_key), 20, now)
            con.execute(
                "INSERT INTO sessions VALUES(?,?,?)",
                (digest(token), digest(csrf), now + self.settings.session_ttl_seconds),
            )
        return token, csrf

    def _session_record(self, token):
        if not token or len(token) > 100:
            raise StoreError("SESSION_REQUIRED", 401)
        with self.connection() as con:
            row = con.execute(
                "SELECT * FROM sessions WHERE token_hash=? AND expires>?", (digest(token), time.time())
            ).fetchone()
        if not row:
            raise StoreError("SESSION_EXPIRED", 401)
        return row

    def resume_session(self, token):
        row = self._session_record(token)
        # Resume without rotating identity or extending its original expiry.
        return session_csrf(token), max(1, int(row["expires"] - time.time()))

    def authenticate_session(self, token, csrf=None):
        row = self._session_record(token)
        # Keep the original random CSRF token valid for pre-upgrade browser tabs.
        if csrf is not None and not (
            secrets.compare_digest(row["csrf_hash"], digest(csrf))
            or secrets.compare_digest(digest(session_csrf(token)), digest(csrf))
        ):
            raise StoreError("CSRF_REJECTED", 403)
        return "session:" + digest(token)

    def enqueue(self, owner, operation_id, request, *, private=False):
        now, run_id = time.time(), secrets.token_urlsafe(24)
        if "contract_v1" in request:
            run_id = "run-" + run_id
        payload = json.dumps(request, sort_keys=True, separators=(",", ":"))
        with self.transaction() as con:
            old = con.execute(
                "SELECT * FROM runs WHERE owner=? AND operation_id=?", (owner, operation_id)
            ).fetchone()
            if old:
                if old["request_hash"] != digest(payload):
                    raise StoreError("IDEMPOTENCY_CONFLICT")
                return old["id"], False
            retained = con.execute("SELECT count(*) FROM runs").fetchone()[0]
            if retained >= self.settings.max_retained_runs:
                raise StoreError("RETENTION_CAPACITY", 429)
            self._limit(con, "runs-global", self.settings.global_runs_per_minute, now)
            self._limit(con, "runs:" + owner, self.settings.runs_per_minute, now)
            active = con.execute(
                "SELECT count(*) FROM runs WHERE status IN ('ACCEPTED','RUNNING')"
            ).fetchone()[0]
            own = con.execute(
                "SELECT count(*) FROM runs WHERE owner=? AND status IN ('ACCEPTED','RUNNING')", (owner,)
            ).fetchone()[0]
            if active >= self.settings.max_active or own >= self.settings.max_per_owner:
                raise StoreError("CONCURRENCY_LIMIT", 429)
            con.execute(
                "INSERT INTO runs(id,owner,operation_id,request_hash,request,mode,created,expires) VALUES(?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    owner,
                    operation_id,
                    digest(payload),
                    payload,
                    request["mode"],
                    now,
                    now + (30 * 86400 if private else self.settings.run_ttl_seconds),
                ),
            )
            funds = request["input"]["available_funds_microusd"]
            self._account(con, "run:" + run_id, funds)
        return run_id, True

    @staticmethod
    def _fence(con, run_id, claim):
        row = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row or row["claim"] != claim or row["status"] != "RUNNING" or row["lease"] <= time.time():
            raise LeaseLost()
        return row

    def claim(self):
        now = time.time()
        with self.transaction() as con:
            row = con.execute(
                "SELECT * FROM runs WHERE expires>? AND (status='ACCEPTED' OR (status='RUNNING' AND lease<?)) ORDER BY created LIMIT 1",
                (now, now),
            ).fetchone()
            if not row:
                return None
            con.execute(
                "UPDATE runs SET status='RUNNING',claim=claim+1,lease=? WHERE id=?",
                (now + self.settings.lease_seconds, row["id"]),
            )
            result = dict(row)
            result["claim"] += 1
            result["request"] = json.loads(result["request"])
            return result

    def heartbeat(self, run_id, claim):
        with self.transaction() as con:
            self._fence(con, run_id, claim)
            con.execute(
                "UPDATE runs SET lease=? WHERE id=?", (time.time() + self.settings.lease_seconds, run_id)
            )

    def emit(self, run_id, claim, event_key, event):
        with self.transaction() as con:
            self._fence(con, run_id, claim)
            old = con.execute(
                "SELECT payload FROM events WHERE run_id=? AND event_key=?", (run_id, event_key)
            ).fetchone()
            if old:
                return json.loads(old[0])
            seq = con.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            event.update(sequence=seq, occurred_at=utc(), event_id=f"{run_id}:{seq}")
            con.execute("INSERT INTO events VALUES(?,?,?,?)", (run_id, event_key, seq, json.dumps(event)))
        return event

    def reserve(self, run_id, claim, attempt_id, amount):
        with self.transaction() as con:
            run = self._fence(con, run_id, claim)
            if type(amount) is not int or amount < 0:
                raise StoreError("INVALID_RESERVATION")
            old = con.execute(
                "SELECT * FROM attempts WHERE run_id=? AND attempt_id=?", (run_id, attempt_id)
            ).fetchone()
            if old:
                if old["amount"] != amount:
                    raise StoreError("RESERVATION_CONFLICT")
                return
            accounts = ["run:" + run_id]
            if run["owner"].startswith("app:"):
                accounts += [run["owner"], "global"]
            for account in accounts:
                row = con.execute("SELECT * FROM budgets WHERE account=?", (account,)).fetchone()
                if not row or row["used"] + row["reserved"] + amount > row["cap"]:
                    raise StoreError("BUDGET_EXHAUSTED", 402)
            for account in accounts:
                con.execute("UPDATE budgets SET reserved=reserved+? WHERE account=?", (amount, account))
            con.execute(
                "INSERT INTO attempts VALUES(?,?,?,?,?,NULL)",
                (run_id, attempt_id, amount, "RESERVED", json.dumps(accounts)),
            )

    def settle(self, run_id, claim, attempt_id, actual, payload):
        with self.transaction() as con:
            self._fence(con, run_id, claim)
            row = con.execute(
                "SELECT * FROM attempts WHERE run_id=? AND attempt_id=?", (run_id, attempt_id)
            ).fetchone()
            if not row or not 0 <= actual <= row["amount"]:
                raise StoreError("INVALID_SETTLEMENT")
            if type(actual) is not int or payload.get("cost_microusd") != actual:
                raise StoreError("INVALID_SETTLEMENT")
            if row["state"] == "SETTLED":
                if json.loads(row["payload"]) != payload:
                    raise StoreError("USAGE_CONFLICT")
                return
            for account in json.loads(row["accounts"]):
                con.execute(
                    "UPDATE budgets SET reserved=reserved-?,used=used+? WHERE account=?",
                    (row["amount"], actual, account),
                )
            con.execute(
                "UPDATE attempts SET state='SETTLED',payload=? WHERE run_id=? AND attempt_id=?",
                (json.dumps(payload), run_id, attempt_id),
            )

    @staticmethod
    def _release(con, run_id):
        # Only mock calls are executed by this queue. No ambiguous real provider charges are released.
        rows = con.execute("SELECT * FROM attempts WHERE run_id=? AND state='RESERVED'", (run_id,)).fetchall()
        for row in rows:
            for account in json.loads(row["accounts"]):
                con.execute(
                    "UPDATE budgets SET reserved=reserved-? WHERE account=?", (row["amount"], account)
                )
        con.execute("UPDATE attempts SET state='RELEASED' WHERE run_id=? AND state='RESERVED'", (run_id,))

    def finish(self, run_id, claim, result):
        with self.transaction() as con:
            self._fence(con, run_id, claim)
            self._release(con, run_id)
            con.execute(
                "UPDATE runs SET status=?,completed=?,lease=0,result=? WHERE id=?",
                (result["status"], time.time(), json.dumps(result), run_id),
            )

    def get(self, run_id, owner):
        with self.connection() as con:
            row = con.execute(
                "SELECT * FROM runs WHERE id=? AND owner=? AND expires>?", (run_id, owner, time.time())
            ).fetchone()
            if not row:
                raise StoreError("RUN_NOT_FOUND", 404)
            return dict(row)

    def events(self, run_id, after=0):
        with self.connection() as con:
            return [
                json.loads(row[0])
                for row in con.execute(
                    "SELECT payload FROM events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT 100",
                    (run_id, after),
                )
            ]

    def usage(self, run_id):
        with self.connection() as con:
            return [
                json.loads(row[0])
                for row in con.execute(
                    "SELECT payload FROM attempts WHERE run_id=? AND state='SETTLED' ORDER BY attempt_id",
                    (run_id,),
                )
            ]

    def ledger(self, owner):
        with self.connection() as con:
            rows = con.execute(
                "SELECT a.payload FROM attempts a JOIN runs r ON r.id=a.run_id WHERE r.owner=? AND a.state='SETTLED' ORDER BY r.created DESC,a.attempt_id LIMIT 1000",
                (owner,),
            ).fetchall()
            budget = con.execute("SELECT cap,used,reserved FROM budgets WHERE account=?", (owner,)).fetchone()
        return {
            "schema_version": "1.1",
            "usage": [json.loads(row[0]) for row in rows],
            "budget": dict(budget) if budget else None,
            "kind": "SIMULATED",
            "maximum_records": 1000,
            "external_llm_spend_microusd": 0,
        }

    def cancel(self, run_id, owner):
        with self.transaction() as con:
            row = con.execute(
                "SELECT * FROM runs WHERE id=? AND owner=? AND expires>?", (run_id, owner, time.time())
            ).fetchone()
            if not row:
                raise StoreError("RUN_NOT_FOUND", 404)
            if row["status"] in ("ACCEPTED", "RUNNING"):
                self._release(con, run_id)
                con.execute(
                    "UPDATE runs SET status='CANCELLED',completed=?,lease=0,claim=claim+1 WHERE id=?",
                    (time.time(), run_id),
                )

    def cleanup(self):
        now = time.time()
        with self.transaction() as con:
            for row in con.execute("SELECT id FROM runs WHERE expires<?", (now,)).fetchall():
                self._release(con, row["id"])
                con.execute("DELETE FROM runs WHERE id=?", (row["id"],))
                con.execute("DELETE FROM budgets WHERE account=?", ("run:" + row["id"],))
            con.execute("DELETE FROM sessions WHERE expires<?", (now,))
            con.execute("DELETE FROM limits WHERE bucket<?", (int(now // 60) - 2,))

    def backup(self, target):
        target = Path(target)
        if target.exists():
            raise ValueError("Backup target already exists")
        with self.connection() as source, sqlite3.connect(target) as destination:
            source.backup(destination)
        target.chmod(0o600)
