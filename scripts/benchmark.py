"""Bounded local measurement, not a provider benchmark or a capacity claim."""

import argparse
import concurrent.futures
import json
import math
import os
import platform
import resource
import shutil
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from agentops.catalog import presets
from agentops.contracts import SimulationInput, SimulationStart
from agentops.engine import Engine, filter_candidates
from agentops.settings import Settings
from agentops.store import Store


def percentile(values, fraction):
    return sorted(values)[min(len(values) - 1, math.ceil(len(values) * fraction) - 1)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="docs/evidence/benchmark.json")
    parser.add_argument("--operations-per-user", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.operations_per_user <= 5:
        parser.error("Use 1 to 5 operations per virtual user")
    specs = [p["input"] for p in presets() if p["id"] in ("economical", "outage", "rate-limit", "malformed")]
    # Pure routing overhead: independent of durable simulation orchestration.
    samples = []
    for _ in range(20):
        filter_candidates(SimulationInput())
    for _ in range(1000):
        start = time.perf_counter()
        filter_candidates(SimulationInput())
        samples.append((time.perf_counter() - start) * 1000)
    report = {
        "kind": "LOCAL_DETERMINISTIC_BENCHMARK",
        "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hardware": {
            "os": platform.system(),
            "architecture": platform.machine(),
            "logical_cpus": os.cpu_count(),
            "disk_free_bytes": shutil.disk_usage(".").free,
        },
        "method": {
            "driver": "Python ThreadPoolExecutor",
            "operations_per_user": args.operations_per_user,
            "mix": ["economical", "outage", "rate-limit", "malformed"],
            "warmup_runs": 4,
            "threshold": "100% expected successful fixture runs, no invariant failures",
            "limits": "Short bounded test. No sustained capacity, real model, network, or production claim.",
            "concurrency": "Virtual users are client threads invoking the engine directly; SQLite serializes writes.",
            "cpu_ram_sampling": "Process CPU seconds and process RSS high-water mark; no host saturation claim.",
        },
        "routing_filter_ms": {
            "samples": len(samples),
            "p50": statistics.median(samples),
            "p95": percentile(samples, 0.95),
        },
        "runs": [],
    }
    try:
        report["hardware"]["ram_bytes"] = int(
            subprocess.check_output(["sysctl", "-n", "hw.memsize"], stderr=subprocess.DEVNULL, text=True)
        )
        report["hardware"]["ram_source"] = "sysctl -n hw.memsize"
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
        report["hardware"]["ram_bytes"] = None
    try:
        report["source_revision"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except subprocess.CalledProcessError:
        report["source_revision"] = None
    for users in [1, 10, 25, 50]:
        with tempfile.TemporaryDirectory() as temp:
            store = Store(
                Settings(
                    db_path=Path(temp) / "bench.db",
                    workers=0,
                    max_active=100,
                    max_per_owner=100,
                    global_runs_per_minute=10000,
                    runs_per_minute=10000,
                )
            )

            def operation(index, store=store):
                start = time.perf_counter()
                body = SimulationStart(input=SimulationInput(**specs[index % len(specs)])).model_dump()
                run_id, _ = store.enqueue("session:benchmark", str(index), body)
                # Each client must execute its own claimed job; other clients may claim it first.
                claimed = store.claim()
                if claimed:
                    Engine(store).execute(claimed)
                while True:
                    row = store.get(run_id, "session:benchmark")
                    if row["status"] not in ("ACCEPTED", "RUNNING"):
                        break
                    time.sleep(0.001)
                return (time.perf_counter() - start) * 1000, row["status"] == "SUCCEEDED"

            for i in range(-4, 0):
                operation(i)
            cpu_before = time.process_time()
            wall = time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=users) as pool:
                results = list(pool.map(operation, range(users * args.operations_per_user)))
            duration = time.perf_counter() - wall
            timings = [r[0] for r in results]
            with store.connection() as con:
                budgets = con.execute("SELECT min(cap-used-reserved) FROM budgets").fetchone()[0]
            report["runs"].append(
                {
                    "virtual_users": users,
                    "operations": len(results),
                    "duration_seconds": duration,
                    "process_cpu_seconds": time.process_time() - cpu_before,
                    "process_max_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    * (1 if platform.system() == "Darwin" else 1024),
                    "p50_ms": statistics.median(timings),
                    "p95_ms": percentile(timings, 0.95),
                    "throughput_per_second": len(results) / duration,
                    "successes": sum(r[1] for r in results),
                    "errors": sum(not r[1] for r in results),
                    "budget_invariant_pass": budgets >= 0,
                }
            )
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
