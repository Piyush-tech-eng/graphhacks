"""Tenant-isolated graph access.

The fixture store is the deterministic demo implementation.  Its public methods map
directly to the Cypher operations in ``scripts/seed_falkordb.py``, so production
deployment can use named FalkorDB graphs without changing the investigation loop.
"""

from __future__ import annotations

from collections import deque
import json
from typing import Any

from .fixtures import get_topology
from .models import ActionPlan, Incident, Observation, Selection, Service


class FixtureGraphStore:
    """One immutable service graph per tenant, loaded only from seeded fixtures."""

    mode = "fixture"

    def __init__(self) -> None:
        self._topologies: dict[str, dict[str, Any]] = {}
        self._inventory_overrides: dict[tuple[str, str], bool] = {}

    def _topology(self, tenant_id: str) -> dict[str, Any]:
        if tenant_id not in self._topologies:
            self._topologies[tenant_id] = get_topology(tenant_id)
        return self._topologies[tenant_id]

    def configure_scenario(self, tenant_id: str, inventory_complete: dict[str, bool] | None = None) -> None:
        for service_id, complete in (inventory_complete or {}).items():
            self._inventory_overrides[(tenant_id, service_id)] = complete

    def service(self, tenant_id: str, service_id: str) -> Service:
        topology = self._topology(tenant_id)
        data = topology["services"].get(service_id)
        if data is None:
            raise KeyError(f"Service {service_id!r} does not belong to tenant {tenant_id!r}")
        return Service(
            id=service_id,
            name=data["name"],
            team=data["team"],
            criticality=data["criticality"],
            status=data.get("status", "healthy"),
            dependency_inventory_complete=self._inventory_overrides.get((tenant_id, service_id), True),
        )

    def services(self, tenant_id: str) -> list[Service]:
        return [self.service(tenant_id, service_id) for service_id in self._topology(tenant_id)["services"]]

    def dependencies(self, tenant_id: str, service_id: str, max_depth: int = 2) -> list[list[str]]:
        self.service(tenant_id, service_id)  # validates tenant boundary first
        topology = self._topology(tenant_id)
        result: list[list[str]] = []
        queue: deque[tuple[str, list[str]]] = deque([(service_id, [service_id])])
        while queue:
            current, path = queue.popleft()
            if len(path) >= max_depth + 1:
                continue
            for dependency in topology["dependencies"].get(current, []):
                next_path = [*path, dependency]
                result.append(next_path)
                queue.append((dependency, next_path))
        return result

    def reverse_dependents(self, tenant_id: str, service_id: str, max_depth: int = 5) -> tuple[list[Service], bool]:
        """Follow ``dependent -> DEPENDS_ON -> target`` backwards for blast radius."""
        self.service(tenant_id, service_id)
        topology = self._topology(tenant_id)
        reverse: dict[str, list[str]] = {candidate: [] for candidate in topology["services"]}
        for dependent, dependencies in topology["dependencies"].items():
            for dependency in dependencies:
                reverse.setdefault(dependency, []).append(dependent)

        discovered: list[Service] = []
        complete = self.service(tenant_id, service_id).dependency_inventory_complete
        queue: deque[tuple[str, int]] = deque([(service_id, 0)])
        visited = {service_id}
        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            current_service = self.service(tenant_id, current)
            complete = complete and current_service.dependency_inventory_complete
            for dependent in reverse.get(current, []):
                if dependent in visited:
                    continue
                visited.add(dependent)
                dependent_service = self.service(tenant_id, dependent)
                complete = complete and dependent_service.dependency_inventory_complete
                discovered.append(dependent_service)
                queue.append((dependent, depth + 1))
        return sorted(discovered, key=lambda item: (-item.criticality, item.name)), complete

    def context(self, tenant_id: str, service_id: str, max_depth: int = 2) -> dict[str, Any]:
        service = self.service(tenant_id, service_id)
        paths = self.dependencies(tenant_id, service_id, max_depth)
        return {
            "service": service,
            "dependency_paths": paths,
            "dependencies": [self.service(tenant_id, path[-1]) for path in paths if len(path) == 2],
        }

    def graph_facts(self, tenant_id: str, service_id: str, max_depth: int = 2) -> list[str]:
        facts = []
        for path in self.dependencies(tenant_id, service_id, max_depth):
            named_path = " depends on ".join(self.service(tenant_id, item).name for item in path)
            facts.append(named_path)
        return facts

    def health(self) -> dict[str, Any]:
        return {"mode": self.mode, "connected": True, "detail": "deterministic fixture graphs"}

    def transition_prior(self, tenant_id: str, last_tool_id: str | None, next_tool_id: str, default: float) -> float:
        """Workflow-graph signal used by GraphRoute after safe candidates exist."""
        return default

    def transition_prior_details(self, tenant_id: str, last_tool_id: str | None, next_tool_id: str, default: float) -> tuple[float, str]:
        if not last_tool_id:
            return default, "static GraphRoute prior (cold start)"
        return self.transition_prior(tenant_id, last_tool_id, next_tool_id, default), "static GraphRoute prior"

    def record_incident(self, incident: Incident) -> None:
        """Persist runtime graph facts when the configured store supports it."""

    def record_selection(self, incident: Incident, selection: Selection) -> None:
        """Persist an inspectable workflow selection when supported."""

    def record_observation(self, incident: Incident, observation: Observation) -> None:
        """Persist an evidence record when supported."""

    def record_action_plan(self, incident: Incident, plan: ActionPlan) -> None:
        """Persist an approval-gated recovery proposal when supported."""


class FalkorDBGraphStore(FixtureGraphStore):
    """Optional adapter hook for a named FalkorDB graph per tenant.

    The hackathon-safe fixture implementation remains available whenever the graph
    server is unreachable.  This class intentionally does not silently issue raw,
    user-provided Cypher: callers pass stable ids and bounded traversal depths.
    """

    mode = "falkordb"

    def __init__(self, host: str = "localhost", port: int = 6379) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self._connected = False
        try:
            from falkordb import FalkorDB  # type: ignore

            self._client = FalkorDB(host=host, port=port)
            self._client.select_graph("graphops_acme").query("RETURN 1")
            self._connected = True
        except Exception as exc:  # optional runtime integration: deliberately fail closed to fixtures
            self._connection_error = str(exc)

    @staticmethod
    def graph_name(tenant_id: str) -> str:
        if not tenant_id.replace("_", "").isalnum():
            raise ValueError("Tenant id must be alphanumeric or underscore")
        return f"graphops_{tenant_id}"

    def health(self) -> dict[str, Any]:
        if self._connected:
            return {"mode": self.mode, "connected": True, "detail": "named tenant graphs enabled"}
        return {"mode": "fixture-fallback", "connected": False, "detail": getattr(self, "_connection_error", "FalkorDB unavailable")}

    def _rows(self, tenant_id: str, query: str, params: dict[str, Any]) -> list[list[Any]]:
        if not self._connected:
            raise ConnectionError("FalkorDB is unavailable")
        result = self._client.select_graph(self.graph_name(tenant_id)).query(query, params)
        return list(result.result_set)

    def service(self, tenant_id: str, service_id: str) -> Service:
        if not self._connected:
            return super().service(tenant_id, service_id)
        rows = self._rows(tenant_id, "MATCH (s:Service {id: $service_id}) RETURN s.id, s.name, s.team, s.criticality, s.status, s.dependency_inventory_complete", {"service_id": service_id})
        if not rows:
            raise KeyError(f"Service {service_id!r} does not belong to tenant {tenant_id!r}")
        row = rows[0]
        return Service(id=row[0], name=row[1], team=row[2], criticality=int(row[3]), status=row[4], dependency_inventory_complete=self._inventory_overrides.get((tenant_id, service_id), bool(row[5])))

    def dependencies(self, tenant_id: str, service_id: str, max_depth: int = 2) -> list[list[str]]:
        if not self._connected:
            return super().dependencies(tenant_id, service_id, max_depth)
        if not 1 <= max_depth <= 5:
            raise ValueError("max_depth must be between 1 and 5")
        rows = self._rows(tenant_id, f"MATCH p=(s:Service {{id: $service_id}})-[:DEPENDS_ON*1..{max_depth}]->(:Service) RETURN [node IN nodes(p) | node.id]", {"service_id": service_id})
        return [list(row[0]) for row in rows]

    def reverse_dependents(self, tenant_id: str, service_id: str, max_depth: int = 5) -> tuple[list[Service], bool]:
        if not self._connected:
            return super().reverse_dependents(tenant_id, service_id, max_depth)
        if not 1 <= max_depth <= 5:
            raise ValueError("max_depth must be between 1 and 5")
        rows = self._rows(tenant_id, f"MATCH (dependent:Service)-[:DEPENDS_ON*1..{max_depth}]->(target:Service {{id: $service_id}}) RETURN DISTINCT dependent.id, dependent.name, dependent.team, dependent.criticality, dependent.status, dependent.dependency_inventory_complete", {"service_id": service_id})
        target = self.service(tenant_id, service_id)
        dependents = [Service(id=row[0], name=row[1], team=row[2], criticality=int(row[3]), status=row[4], dependency_inventory_complete=self._inventory_overrides.get((tenant_id, row[0]), bool(row[5]))) for row in rows]
        complete = target.dependency_inventory_complete and all(item.dependency_inventory_complete for item in dependents)
        return sorted(dependents, key=lambda item: (-item.criticality, item.name)), complete

    def transition_prior(self, tenant_id: str, last_tool_id: str | None, next_tool_id: str, default: float) -> float:
        if not self._connected or not last_tool_id:
            return default
        rows = self._rows(
            tenant_id,
            "MATCH (:Tool {id: $last_tool_id})-[r:COMMONLY_FOLLOWS]->(:Tool {id: $next_tool_id}) RETURN r.success_rate LIMIT 1",
            {"last_tool_id": last_tool_id, "next_tool_id": next_tool_id},
        )
        return float(rows[0][0]) if rows and rows[0][0] is not None else default

    def transition_prior_details(self, tenant_id: str, last_tool_id: str | None, next_tool_id: str, default: float) -> tuple[float, str]:
        if not self._connected or not last_tool_id:
            source = "static GraphRoute prior (cold start)" if not last_tool_id else "static GraphRoute fallback (FalkorDB unavailable)"
            return default, source
        rows = self._rows(
            tenant_id,
            "MATCH (:Tool {id: $last_tool_id})-[r:COMMONLY_FOLLOWS]->(:Tool {id: $next_tool_id}) RETURN r.success_rate LIMIT 1",
            {"last_tool_id": last_tool_id, "next_tool_id": next_tool_id},
        )
        if rows and rows[0][0] is not None:
            return float(rows[0][0]), "configured FalkorDB COMMONLY_FOLLOWS edge"
        return default, "static GraphRoute fallback (no matching FalkorDB edge)"

    def _expect_write(self, tenant_id: str, query: str, params: dict[str, Any], record: str) -> None:
        rows = self._rows(tenant_id, query, params)
        if not rows:
            raise RuntimeError(f"FalkorDB did not persist {record}; verify the tenant graph is seeded")

    def record_incident(self, incident: Incident) -> None:
        if not self._connected:
            return
        self._expect_write(
            incident.tenant_id,
            "MERGE (i:Incident {id: $id}) SET i.summary = $summary, i.severity = $severity, i.scenario_id = $scenario_id, i.status = $status "
            "MERGE (r:TaskRun {id: $run_id}) SET r.incident_id = $id, r.tenant_id = $tenant_id, r.started_at = $started_at, r.selection_policy = 'graphroute-v2' "
            "MERGE (i)-[:HAS_RUN]->(r) RETURN r.id",
            {
                "id": incident.id, "summary": incident.summary, "severity": incident.severity,
                "scenario_id": incident.scenario_id, "status": incident.status, "run_id": incident.id,
                "tenant_id": incident.tenant_id, "started_at": incident.created_at,
            },
            "incident and task run",
        )

    def record_selection(self, incident: Incident, selection: Selection) -> None:
        if not self._connected:
            return
        self._expect_write(
            incident.tenant_id,
            "MATCH (t:Tool {id: $tool_id}), (r:TaskRun {id: $run_id}) "
            "MERGE (t)-[s:SELECTED_FOR]->(r) "
            "SET s.rank = $rank, s.score = $score, s.target_service_id = $target_service_id, s.reason_codes = $reason_codes "
            "RETURN t.id",
            {
                "tool_id": selection.tool_id, "run_id": incident.id, "rank": selection.rank,
                "score": selection.score, "target_service_id": selection.target_service_id,
                "reason_codes": selection.reason_codes,
            },
            "tool selection",
        )

    def record_observation(self, incident: Incident, observation: Observation) -> None:
        if not self._connected:
            return
        self._expect_write(
            incident.tenant_id,
            "MERGE (o:Observation {id: $id}) "
            "SET o.kind = $kind, o.source = $source, o.timestamp = $timestamp, o.service_id = $service_id, "
            "o.summary = $summary, o.fixture_id = $fixture_id, o.tool_run_id = $tool_run_id, o.values_json = $values_json "
            "WITH o MATCH (s:Service {id: $service_id}), (i:Incident {id: $incident_id}) "
            "MERGE (o)-[:OBSERVED_ON]->(s) MERGE (i)-[:HAS_OBSERVATION]->(o) RETURN o.id",
            {
                "id": observation.id, "kind": observation.kind, "source": observation.source,
                "timestamp": observation.timestamp, "service_id": observation.service_id,
                "summary": observation.summary, "fixture_id": observation.fixture_id,
                "tool_run_id": observation.tool_run_id, "values_json": json.dumps(observation.values, sort_keys=True),
                "incident_id": incident.id,
            },
            "observation",
        )

    def record_action_plan(self, incident: Incident, plan: ActionPlan) -> None:
        if not self._connected:
            return
        self._expect_write(
            incident.tenant_id,
            "MERGE (p:ActionPlan {id: $id}) "
            "SET p.action_type = $action_type, p.target_id = $target_service_id, p.status = $status, "
            "p.simulation_only = true, p.impact_complete = $impact_complete, p.blocked_reason = $blocked_reason, "
            "p.hypothesis_id = $hypothesis_id, p.supporting_observation_ids = $supporting_observation_ids "
            "WITH p MATCH (i:Incident {id: $incident_id}), (s:Service {id: $target_service_id}) "
            "MERGE (i)-[:PROPOSES]->(p) MERGE (p)-[:TARGETS]->(s) RETURN p.id",
            {
                "id": plan.id, "action_type": plan.action_type, "target_service_id": plan.target_service_id,
                "status": plan.status, "impact_complete": plan.impact_complete,
                "blocked_reason": plan.blocked_reason or "", "incident_id": incident.id,
                "hypothesis_id": plan.hypothesis_id or "",
                "supporting_observation_ids": plan.supporting_observation_ids,
            },
            "action plan",
        )
