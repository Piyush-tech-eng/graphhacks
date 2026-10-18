# GraphOps

GraphOps is a safe, graph-guided incident-investigation demo. It starts from a synthetic alert, follows a tenant-isolated service-dependency graph, records evidence-backed hypotheses, calculates a reverse-dependency blast radius, and pauses for explicit approval before running a **fixture-only simulation**.

It implements the supplied GraphOps and GraphRoute v2 designs as a focused MVP:

- FalkorDB named graphs (`graphops_acme`, `graphops_northstar`) provide structural tenant isolation when enabled.
- GraphRoute never depends solely on the previous tool: first-call candidates use the incident target, graph context, and unresolved evidence gaps.
- Tool selection records score components, reason codes, and graph facts in a persisted run trace.
- Audit events are stored in a local SQLite database at `.graphops/audit.sqlite3` by default; set `GRAPHOPS_TRACE_DB` to choose another path.
- Observations are deterministic, typed, and fixture-backed. `no_data` never means healthy.
- Hypotheses cite observation IDs from the current incident; validation rejects missing IDs, out-of-scope services, stale timestamps, and root-cause claims without a dependency counter-check.
- Recovery is a medium-risk plan, not an execution command. Unknown graph impact blocks it; a known-safe plan still requires approval; simulation never contacts external infrastructure.

## Quick start

Requires Python 3.11+.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m uvicorn graphops.api:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Pick **Checkout API HTTP 500 spike**, run to the recovery plan, approve the explicitly labelled simulation, then simulate it. The trace shows every graph fact, selection decision, observation, and safety decision.

To launch the dashboard after setup:

```powershell
.\scripts\start_demo.ps1
```

Open `http://127.0.0.1:8000`; opening `static/index.html` directly does not start the API.

### macOS and Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m uvicorn graphops.api:app --reload
```

Open `http://127.0.0.1:8000` in a browser. The persisted SQLite trace survives process restarts, while active incident state and approval state are kept in memory and are not restored after restart.

To exercise the core and HTTP routes without starting a separate server:

```powershell
python scripts/run_benchmark.py
python -m unittest discover -s tests -v
python -m pytest -q
```

## FalkorDB mode

The app is runnable by default with deterministic in-process graph fixtures so judges can reproduce the incident loop without external infrastructure. To use the FalkorDB-backed graph path:

```powershell
docker compose up -d
python scripts/seed_falkordb.py
$env:FALKORDB_ENABLED = "true"
python -m uvicorn graphops.api:app --reload
```

The seed script resets and rebuilds one named graph per tenant. With `FALKORDB_ENABLED=true`, service-context and blast-radius lookups use those graphs, and GraphRoute reads `COMMONLY_FOLLOWS` transition priors from them. If FalkorDB is unavailable, `/health` reports `fixture-fallback` rather than pretending fresh graph data was retrieved.

## API

| Route | What it does |
| --- | --- |
| `GET /health` | Process and graph-store status |
| `GET /api/scenarios` | Available synthetic incidents |
| `POST /api/incidents` | Create an investigation from `{ "scenario_id": "db-pool-exhaustion" }` |
| `POST /api/incidents/{id}/step` | Run exactly one safe, bounded diagnostic step |
| `POST /api/incidents/{id}/run` | Run the bounded loop until it proposes or blocks a plan |
| `GET /api/incidents/{id}` | Current incident, evidence ledger, graph context, and plan |
| `GET /api/incidents/{id}/trace` | Chronological persisted audit trace |
| `POST /api/action-plans/{id}/approve` | Record human approval for a simulation |
| `POST /api/action-plans/{id}/simulate` | Run the approved fixture-only simulator |
| `POST /api/benchmark` | Replay the deterministic fixture suite |

## Demo stories

1. **Database-pool exhaustion** — `checkout-api` leads through `orders-db` metrics and logs. The plan lists direct and transitive dependents before a simulated restart.
2. **Bad checkout deployment** — a recent release plus a matching application log supports a simulated rollback proposal.
3. **Incomplete inventory** — the action is blocked because unknown impact is not treated as zero impact.
4. **Northstar identity deployment** — exercises the second tenant graph and proposes a simulated rollback after checking the Identity API's database dependency.

The two tenants have different service graphs; a lookup for an Acme service cannot resolve inside the Northstar graph. The suite verifies first-call routing, direct and transitive blast radius, tenant isolation, evidence-linked approval, simulation gating, and the blocked-impact scenario.

The benchmark's flat baseline is a fixed alert-only mapping by scenario class. It is not an independently executed selector and must not be interpreted as a controlled comparison. The separate catalog-scale 20/60/150/300 tool sweep from the design remains future evaluation work.

## Safety and scope

This is an educational, synthetic-data MVP. Diagnostic outputs come from local fixtures. The simulator only creates an in-memory verification observation and has no shell, cloud, HTTP, credential, notification, or production-control capability. It does not claim production readiness, autonomous remediation, or benchmark gains outside the reported fixture suite.

AI assistance disclosure: This project was developed with OpenAI Codex; its implementation and test results are documented in this repository.

## Project layout

```text
src/graphops/
  api.py          FastAPI surface and dashboard host
  engine.py       bounded investigation loop and audit trace integration
  graph_store.py  tenant graph queries and FalkorDB adapter
  graphroute.py   cold-start-safe candidate ranking
  tools.py        deterministic bounded diagnostic contracts
  evidence.py     evidence ledger and hypothesis checks
  safety.py       fail-closed impact gate and simulation
  trace_store.py  durable local audit event storage
  static/         self-contained incident workspace
scripts/
  seed_falkordb.py
  run_benchmark.py
tests/
  test_graphops.py
```
