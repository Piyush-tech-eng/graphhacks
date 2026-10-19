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
python scripts/run_catalog_evaluation.py
python -m unittest discover -s tests -v
python -m pytest -q
node tests/test_ui_state.js
```

The dashboard presents one authoritative incident view from the API: changing the scenario clears the active evidence and plan; the graph-mode badge comes from `/health`; and the connected-data view is rendered from tenant-scoped nodes and `DEPENDS_ON` edges. The evidence timeline links tool choices, observations, hypothesis updates, recovery checks, approval, and simulated verification. The trace can be exported as JSON. Active incident state is still in memory and is not restored after a server restart.

## Deploying on Render

The repository includes a `render.yaml` Blueprint for a Python web service. In the Render dashboard, create a Blueprint from this repository and deploy the `main` branch. The service installs the project, starts Uvicorn on Render's assigned port, and uses `/health` for its health check. The default Blueprint uses Render's free plan in Singapore; free services can spin down while idle. Incident objects are held in process memory, and the default SQLite trace file is on the instance's ephemeral filesystem, so this configuration is for a demo rather than durable multi-instance operation. See [Render's FastAPI deployment guide](https://render.com/docs/deploy-fastapi) and [Blueprint reference](https://render.com/docs/blueprint-spec).

## FalkorDB mode

The app is runnable by default with deterministic in-process graph fixtures so judges can reproduce the incident loop without external infrastructure. To use the FalkorDB-backed graph path:

```powershell
docker compose up -d
python scripts/seed_falkordb.py
$env:FALKORDB_ENABLED = "true"
python -m uvicorn graphops.api:app --reload
```

The seed script resets and rebuilds one named graph per tenant. With `FALKORDB_ENABLED=true`, service-context and blast-radius lookups use those graphs, and GraphRoute reads `COMMONLY_FOLLOWS` transition priors from them. If FalkorDB is unavailable, `/health` reports `fixture-fallback` rather than pretending fresh graph data was retrieved.

## Graph data model

Each tenant is isolated in its own named graph (`graphops_acme` or `graphops_northstar`). The fixture store exposes the same service topology and traversal behavior without requiring FalkorDB. FalkorDB seeding creates the structural nodes and edges below; investigation-time writes add run, observation, selection, and action-plan records to the same tenant graph.

| Node label | Main properties | Purpose |
| --- | --- | --- |
| `Service` | `id`, `name`, `team`, `criticality`, `status`, `dependency_inventory_complete` | Tenant service catalog and dependency topology |
| `Tool` | `id`, `name`, `risk_tier`, `enabled` | Allow-listed diagnostic and planning tools |
| `Incident` | `id`, `summary`, `severity`, `scenario_id`, `status` | Synthetic alert being investigated |
| `TaskRun` | `id`, `incident_id`, `tenant_id`, `started_at`, `selection_policy` | Bounded GraphRoute investigation run |
| `Observation` | `id`, `kind`, `source`, `timestamp`, `service_id`, `summary`, `fixture_id`, `tool_run_id`, `values_json` | Typed evidence gathered during a run |
| `ActionPlan` | `id`, `action_type`, `target_id`, `status`, `simulation_only`, `impact_complete`, `blocked_reason`, `hypothesis_id`, `supporting_observation_ids` | Approval-gated, simulation-only recovery proposal |

| Relationship | Direction | Meaning |
| --- | --- | --- |
| `DEPENDS_ON` | `Service` → `Service` | Directed dependency; traversed forward for investigation context and backward for blast radius |
| `COMMONLY_FOLLOWS` | `Tool` → `Tool` | Workflow prior with `frequency` and `success_rate`; a ranking signal, not a hard-coded route |
| `HAS_RUN` | `Incident` → `TaskRun` | Associates the investigation run with its incident |
| `SELECTED_FOR` | `Tool` → `TaskRun` | Records selected tool rank, score, target, and reason codes |
| `HAS_OBSERVATION` | `Incident` → `Observation` | Attaches gathered evidence to the incident |
| `OBSERVED_ON` | `Observation` → `Service` | Identifies the service an observation describes |
| `PROPOSES` | `Incident` → `ActionPlan` | Associates the recovery proposal with the investigated incident |
| `TARGETS` | `ActionPlan` → `Service` | Identifies the proposed action's target service |

Hypotheses are currently an in-memory evidence ledger, not persisted `Hypothesis` nodes. Their IDs and supporting observation IDs are copied onto `ActionPlan` so the proposal retains a traceable link to its diagnosis. The SQLite audit trace is persisted separately; active incident and approval state is not restored after an application restart.

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

The original fixture benchmark's flat baseline is a fixed scenario-class mapping and is not an independently executed selector. A separate reproducible rank-only stress evaluation is available with `python scripts/run_catalog_evaluation.py`; it compares the production GraphRoute score weights with a lexical alert-only baseline on the same six authored tasks, catalogs, and one-call budget. Catalogs use seeded synthetic distractor tools. It does not call tools or infrastructure and is not a production, general-LLM, or end-to-end execution benchmark.

Latest local rank-only evaluation (six synthetic tasks; ranking time is host-dependent):

| Catalog size | GraphRoute top-1 | Lexical baseline top-1 | Candidate evaluations per choice |
| ---: | ---: | ---: | ---: |
| 20 | 100% | 66.7% | 20 |
| 60 | 100% | 33.3% | 60 |
| 150 | 100% | 33.3% | 150 |
| 300 | 100% | 33.3% | 300 |

These small, authored workloads intentionally test ranking under distractor growth; they are not broad evidence of superiority or scale performance. A representative larger task set and end-to-end selector/tool-call evaluation remain future work.

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
