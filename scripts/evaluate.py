"""Golden corpus is separate from the engine and its context."""

import json
import tempfile
from pathlib import Path

from agentops.contracts import SimulationInput, SimulationStart
from agentops.engine import Engine
from agentops.settings import Settings
from agentops.store import Store

corpus = json.loads(Path("evaluations/routing-v1.json").read_text())
results = []
with tempfile.TemporaryDirectory() as temp:
    store = Store(Settings(db_path=Path(temp) / "eval.db", workers=0))
    for case in corpus["cases"]:
        request = SimulationStart(input=SimulationInput(**case["input"])).model_dump()
        store.enqueue("session:evaluation", case["id"], request)
        outcome = Engine(store).execute(store.claim())["output"]
        actual = {key: outcome[key] for key in case["expected"]}
        results.append(
            {
                "id": case["id"],
                "expected": case["expected"],
                "actual": actual,
                "pass": actual == case["expected"],
            }
        )
report = {
    "corpus_version": corpus["version"],
    "mode": "SANDBOX_DETERMINISTIC",
    "cases": results,
    "passed": sum(r["pass"] for r in results),
    "total": len(results),
}
Path("docs/evidence/evaluation.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
raise SystemExit(0 if report["passed"] == report["total"] else 1)
