# Local performance and evaluation

Measured implementation revision: `952b43cac06bda19d7627ee426cdb43fe848e513`. Date UTC: 2026-10-13T10:56:08Z.

The benchmark driver is `scripts/benchmark.py`, using Python ThreadPoolExecutor on macOS arm64, 10 logical CPUs and 24 GiB host RAM. Each cohort uses a disposable SQLite database. Four warmup operations precede five operations per virtual user. The deterministic request mix is economical, primary outage, 429 and malformed response. Provider latency/backoff advances a virtual clock; measured time includes actual routing/SQLite work.

| Concurrent client threads | Measured operations | P50 ms | P95 ms | Cohort seconds | Errors |
|---:|---:|---:|---:|---:|---:|
| 1 | 5 | 18.32 | 23.56 | 0.089 | 0 |
| 10 | 50 | 15.22 | 360.61 | 0.492 | 0 |
| 25 | 125 | 61.82 | 693.63 | 0.894 | 0 |
| 50 | 250 | 370.37 | 1713.56 | 3.043 | 0 |

All 430 measured operations reached the expected successful fixture result, with no budget invariant failures. Per-cohort process CPU time, process RSS high-water mark, disk availability, observed throughput and methodology are in `docs/evidence/benchmark.json`. The largest cohort's process RSS high-water mark was 50.8 MiB. This is a cumulative process high-water mark, not per-container or host-memory consumption.

Pure eligibility/ranking over 1,000 samples after warmup had P50 0.0139 ms and P95 0.0145 ms. That excludes queue, database, network and provider time.

**Limits:** these are short local measurements, not sustainable capacity or a production SLA. The 50-thread cohort shows SQLite write contention; the host differs from the intended small VPS. The driver invokes the durable engine directly, not the HTTP server/worker-pool path. HTTP/network P95, queue dwell distribution, production/VPS load, real providers and soak capacity are **NOT BENCHMARKED**. Do not infer that 50 simultaneous public runs meet a service target.

Reproduce: `uv run python -m scripts.benchmark --operations-per-user 5`. Time and hardware variability are expected. Never run an unbounded stress test against shared production.

## Golden evaluation and costs

`evaluations/routing-v1.json` is a separate versioned corpus. `uv run python -m scripts.evaluate` produced 12/12 matching outcomes. It evaluates capability/policy/budget/fallback behavior, not real LLM quality. All catalog quality values and prices are explicitly synthetic.

The default Cedar fixture costs 280 microdollars ($0.000280); the Harbor fixture costs 660 microdollars for the same 2,000 input/500 output token scenario. A malformed Cedar response followed by successful Harbor costs 940 simulated microdollars, counting the failed attempt. These are fixed-fixture arithmetic checks, **not real savings**. External LLM spend for every simulation is $0. Hosting and development costs are unmeasured, not zero.
