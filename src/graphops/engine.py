"""The evidence-first GraphOps investigation loop."""

from __future__ import annotations

import os
import threading
import uuid
from typing import Any

from .evidence import evaluate_hypotheses, validate_evidence
from .fixtures import get_scenario, scenario_catalog
from .catalog_evaluation import evaluate_catalog_scale
from .graph_store import FalkorDBGraphStore, FixtureGraphStore
from .graphroute import GraphRouteSelector
from .models import Incident, Service, ToolResult, ToolStatus, TraceEvent, plain, now_iso
from .safety import SafetyGate
from .trace_store import SQLiteTraceStore
from .tools import DiagnosticTools


class GraphOpsEngine:
    def __init__(self, graph: FixtureGraphStore | None = None, trace_store: SQLiteTraceStore | None = None) -> None:
        if graph is not None:
            self.graph = graph
        elif os.getenv("FALKORDB_ENABLED", "false").lower() == "true":
            self.graph = FalkorDBGraphStore(os.getenv("FALKORDB_HOST", "localhost"), int(os.getenv("FALKORDB_PORT", "6379")))
        else:
            self.graph = FixtureGraphStore()
        self.tools = DiagnosticTools(self.graph)
        self.selector = GraphRouteSelector(self.graph)
        self.safety = SafetyGate(self.graph)
        self.trace_store = trace_store or SQLiteTraceStore()
        self._incidents: dict[str, Incident] = {}
        self._lock = threading.RLock()

    @staticmethod
    def scenarios() -> list[dict[str, str]]:
        return scenario_catalog()

    def _trace(self, incident: Incident, event_type: str, payload: dict[str, Any]) -> None:
        event = TraceEvent(len(incident.trace) + 1, event_type, now_iso(), plain(payload))
        incident.trace.append(event)
        self.trace_store.append(incident.id, plain(event))

    def create_incident(self, scenario_id: str) -> Incident:
        scenario = get_scenario(scenario_id)
        incident = Incident(
            id=f"inc-{uuid.uuid4().hex[:10]}", scenario_id=scenario_id,
            tenant_id=scenario["tenant_id"], target_service_id=scenario["target_service_id"],
            summary=scenario["summary"], severity=scenario["severity"],
        )
        self.graph.record_incident(incident)
        self._trace(incident, "incident_created", {
            "scenario_id": scenario_id, "tenant_id": incident.tenant_id,
            "target_service_id": incident.target_service_id, "summary": incident.summary,
            "graph_facts": self.graph.graph_facts(incident.tenant_id, incident.target_service_id),
        })
        with self._lock:
            self._incidents[incident.id] = incident
        return incident

    def incident(self, incident_id: str) -> Incident:
        with self._lock:
            if incident_id not in self._incidents:
                raise KeyError(f"Unknown incident: {incident_id}")
            return self._incidents[incident_id]

    @staticmethod
    def _gap(tool_id: str) -> str:
        return {
            "get_service_health": "health",
            "get_service_metrics": "metrics",
            "search_logs": "logs",
            "get_recent_deployments": "deployments",
            "get_dependency_context": "dependency_context",
            "plan_recovery": "recovery_plan",
        }[tool_id]

    def _execute(self, incident: Incident, tool_id: str, target: str) -> ToolResult:
        if tool_id == "get_service_health":
            return self.tools.get_service_health(incident, target)
        if tool_id == "get_service_metrics":
            return self.tools.get_service_metrics(incident, target)
        if tool_id == "search_logs":
            return self.tools.search_logs(incident, target, query_terms=["error", "timeout", "connection", "release"])
        if tool_id == "get_recent_deployments":
            return self.tools.get_recent_deployments(incident, target, window_minutes=60)
        if tool_id == "get_dependency_context":
            return self.tools.get_dependency_context(incident, target, max_depth=2)
        raise ValueError(f"Unsupported automatic diagnostic tool: {tool_id}")

    def step(self, incident_id: str) -> dict[str, Any]:
        """Execute exactly one safe diagnostic or create a recovery proposal."""
        with self._lock:
            incident = self.incident(incident_id)
            if incident.action_plan is not None:
                return {"state": plain(incident), "message": "Investigation already has an action-plan decision."}
            selection = self.selector.select_next(incident)
            if selection is None:
                incident.hypotheses = evaluate_hypotheses(incident)
                incident.status = "insufficient_evidence"
                self._trace(incident, "investigation_abstained", {"reason": "No safe candidate can reduce an unresolved evidence gap."})
                return {"state": plain(incident), "message": "No safe next diagnostic exists; investigation abstained."}

            incident.selections.append(selection)
            self.graph.record_selection(incident, selection)
            self._trace(incident, "tool_selected", plain(selection))
            if selection.tool_id == "plan_recovery":
                incident.resolved_gaps.add("recovery_plan:" + incident.target_service_id)
                incident.hypotheses = evaluate_hypotheses(incident)
                allowed_service_ids = {incident.target_service_id}
                for path in self.graph.dependencies(incident.tenant_id, incident.target_service_id, max_depth=3):
                    allowed_service_ids.update(path)
                validation_errors = {
                    item.id: validate_evidence(incident, item, allowed_service_ids) for item in incident.hypotheses
                }
                if any(validation_errors.values()):
                    incident.status = "insufficient_evidence"
                    self._trace(incident, "evidence_validation_failed", validation_errors)
                    return {"state": plain(incident), "message": "Evidence validation failed; no action plan was created."}
                self._trace(incident, "hypotheses_evaluated", {"hypotheses": incident.hypotheses})
                incident.action_plan = self.safety.propose(incident)
                self.graph.record_action_plan(incident, incident.action_plan)
                incident.status = "action_blocked" if incident.action_plan.status == "blocked" else "awaiting_approval"
                self._trace(incident, "action_plan_proposed", {"action_plan": incident.action_plan})
                return {"state": plain(incident), "selection": plain(selection), "action_plan": plain(incident.action_plan)}

            result = self._execute(incident, selection.tool_id, selection.target_service_id)
            incident.observations.extend(result.observations)
            for observation in result.observations:
                self.graph.record_observation(incident, observation)
            incident.last_tool_id = selection.tool_id
            incident.resolved_gaps.add(f"{self._gap(selection.tool_id)}:{selection.target_service_id}")
            self._trace(incident, "tool_completed", plain(result))
            incident.hypotheses = evaluate_hypotheses(incident)
            self._trace(incident, "hypotheses_evaluated", {"hypotheses": incident.hypotheses})
            return {"state": plain(incident), "selection": plain(selection), "result": plain(result)}

    def run_to_plan(self, incident_id: str, max_steps: int = 16) -> Incident:
        for _ in range(max_steps):
            incident = self.incident(incident_id)
            if incident.action_plan is not None or incident.status == "insufficient_evidence":
                return incident
            self.step(incident_id)
        raise RuntimeError("The safe investigation loop exceeded its bounded step budget")

    def approve(self, plan_id: str) -> Incident:
        with self._lock:
            incident = next((item for item in self._incidents.values() if item.action_plan and item.action_plan.id == plan_id), None)
            if incident is None or incident.action_plan is None:
                raise KeyError(f"Unknown action plan: {plan_id}")
            self.safety.approve(incident.action_plan)
            incident.status = "approved_for_simulation"
            self._trace(incident, "action_plan_approved", {"plan_id": plan_id, "simulation_only": True})
            return incident

    def simulate(self, plan_id: str) -> Incident:
        with self._lock:
            incident = next((item for item in self._incidents.values() if item.action_plan and item.action_plan.id == plan_id), None)
            if incident is None:
                raise KeyError(f"Unknown action plan: {plan_id}")
            observation = self.safety.simulate(incident)
            incident.observations.append(observation)
            self.graph.record_observation(incident, observation)
            self.graph.record_action_plan(incident, incident.action_plan)
            incident.status = "simulated_recovery_verified"
            self._trace(incident, "recovery_simulated", {"action_plan": incident.action_plan, "verification": observation})
            return incident

    def view(self, incident_id: str) -> dict[str, Any]:
        incident = self.incident(incident_id)
        context = self.graph.context(incident.tenant_id, incident.target_service_id)
        selected_target = incident.selections[-1].target_service_id if incident.selections else None
        plan_target = incident.action_plan.target_service_id if incident.action_plan else None
        blast_radius_ids = [item["service_id"] for item in incident.action_plan.blast_radius] if incident.action_plan else []
        dependency_paths: list[list[str]] = []
        direct_dependents: dict[str, Service] = {}
        dependency_inventory_complete = True
        focus_ids = list(dict.fromkeys([incident.target_service_id, selected_target, plan_target]))
        for focus_id in filter(None, focus_ids):
            dependency_paths.extend(self.graph.dependencies(incident.tenant_id, focus_id, max_depth=3))
            dependents, target_inventory_complete = self.graph.reverse_dependents(incident.tenant_id, focus_id, max_depth=5)
            direct_dependents.update({item.id: item for item in dependents})
            dependency_inventory_complete = dependency_inventory_complete and target_inventory_complete
        for service in direct_dependents.values():
            dependency_paths.extend(self.graph.dependencies(incident.tenant_id, service.id, max_depth=3))
        dependency_paths = [list(path) for path in dict.fromkeys(tuple(path) for path in dependency_paths)]
        dependency_ids = list(dict.fromkeys(service_id for path in dependency_paths for service_id in path[1:]))
        nodes_by_id: dict[str, dict[str, Any]] = {}

        def add_node(service_id: str, role: str) -> None:
            service = plain(self.graph.service(incident.tenant_id, service_id))
            current = nodes_by_id.get(service_id)
            roles = set(current["roles"] if current else [])
            roles.add(role)
            nodes_by_id[service_id] = {**service, "roles": sorted(roles)}

        add_node(incident.target_service_id, "incident_target")
        for service_id in dependency_ids:
            add_node(service_id, "upstream_dependency")
        if selected_target:
            add_node(selected_target, "selected_target")
        if plan_target:
            add_node(plan_target, "action_target")
        for service_id in blast_radius_ids:
            add_node(service_id, "blast_radius")
        for service in direct_dependents.values():
            add_node(service.id, "downstream_dependent")
        edges = {
            (path[index], path[index + 1]): {"source": path[index], "target": path[index + 1], "type": "DEPENDS_ON"}
            for path in dependency_paths
            for index in range(len(path) - 1)
        }
        result = plain(incident)
        result["graph_context"] = {
            "service": plain(context["service"]),
            "dependency_paths": dependency_paths,
            "nodes": list(nodes_by_id.values()),
            "edges": list(edges.values()),
            "selected_target_service_id": selected_target,
            "blast_radius_service_ids": blast_radius_ids,
            "dependency_inventory_complete": dependency_inventory_complete,
            "graph_mode": self.graph.health(),
        }
        result["trace"] = self.trace_store.list(incident_id)
        return result

    def persisted_trace(self, incident_id: str) -> list[dict[str, Any]]:
        return self.trace_store.list(incident_id)

    def benchmark(self) -> dict[str, Any]:
        """Run the same deterministic fixtures through GraphOps and a flat baseline."""
        catalog_evaluation = evaluate_catalog_scale()
        cases: list[dict[str, Any]] = []
        for scenario in self.scenarios():
            scenario_definition = get_scenario(scenario["id"])
            isolated = GraphOpsEngine(FixtureGraphStore())
            incident = isolated.create_incident(scenario["id"])
            isolated.run_to_plan(incident.id)
            final = isolated.incident(incident.id)
            leading = next((item.id for item in final.hypotheses if item.status == "leading"), None)
            # A deliberately flat baseline asks only the alerting service and never
            # traverses a dependency edge; it is an explicit comparison mode, not an
            # assertion about a real LLM's performance.
            flat_leading = "h-recent-deployment" if scenario_definition["truth"] == "bad_deployment" else "h-insufficient-evidence"
            expected = scenario_definition["expected_hypothesis"]
            cases.append({
                "scenario_id": scenario["id"], "expected_hypothesis": expected,
                "graphops_hypothesis": leading,
                "flat_baseline_hypothesis": flat_leading,
                "graphops_correct": leading == expected,
                "flat_baseline_correct": flat_leading == expected,
                "graphops_tool_calls": len([event for event in final.trace if event.event_type == "tool_completed"]),
                "action_status": final.action_plan.status if final.action_plan else final.status,
                "unsafe_action_expected": scenario_definition["unsafe_action_expected"],
            })
        total = len(cases)
        unsafe_cases = [item for item in cases if item["unsafe_action_expected"]]
        return {
            "cases": cases,
            **catalog_evaluation,
            "measurement_note": "These are deterministic fixture results, not claims about production incidents or LLM accuracy. The flat baseline is a fixed alert-only scenario-class mapping, not an independently executed selector. The catalog_scale section is a separate offline rank-only synthetic evaluation.",
            "summary": {
                "case_count": total,
                "graphops_top1_accuracy": sum(item["graphops_correct"] for item in cases) / total,
                "flat_baseline_top1_accuracy": sum(item["flat_baseline_correct"] for item in cases) / total,
                "unsafe_action_block_rate": (sum(item["action_status"] == "blocked" for item in unsafe_cases) / len(unsafe_cases)) if unsafe_cases else None,
                "median_graphops_tool_calls": sorted(item["graphops_tool_calls"] for item in cases)[total // 2],
            },
        }
