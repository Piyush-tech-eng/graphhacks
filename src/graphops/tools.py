"""Bounded, deterministic diagnostic tools.  They never call external systems."""

from __future__ import annotations

import time
import uuid
from typing import Any

from .fixtures import get_scenario
from .graph_store import FixtureGraphStore
from .models import Incident, Observation, ToolResult, ToolStatus


class DiagnosticTools:
    def __init__(self, graph: FixtureGraphStore) -> None:
        self.graph = graph

    @staticmethod
    def _run_id() -> str:
        return f"tool-{uuid.uuid4().hex[:10]}"

    @staticmethod
    def _observation(run_id: str, scenario_id: str, kind: str, source: str, service_id: str, summary: str, values: dict[str, Any]) -> Observation:
        return Observation(
            id=f"obs-{uuid.uuid4().hex[:10]}", kind=kind, source=source,
            service_id=service_id, summary=summary, values=values,
            fixture_id=scenario_id, tool_run_id=run_id,
        )

    def _fixture(self, incident: Incident) -> dict[str, Any]:
        return get_scenario(incident.scenario_id)

    def get_service_health(self, incident: Incident, service_id: str) -> ToolResult:
        started = time.perf_counter()
        self.graph.service(incident.tenant_id, service_id)
        run_id = self._run_id()
        payload = self._fixture(incident).get("health", {}).get(service_id)
        if payload is None:
            return ToolResult(run_id, "GetServiceHealth", {"service_id": service_id}, ToolStatus.NO_DATA, duration_ms=round((time.perf_counter()-started)*1000), limitation="No health fixture is available.")
        observation = self._observation(run_id, incident.scenario_id, "health", "synthetic-health-fixture", service_id, f"Health state is {payload['state']}.", payload)
        return ToolResult(run_id, "GetServiceHealth", {"service_id": service_id}, ToolStatus.OK, [observation], round((time.perf_counter()-started)*1000))

    def get_service_metrics(self, incident: Incident, service_id: str, window_minutes: int = 15) -> ToolResult:
        if not 1 <= window_minutes <= 60:
            raise ValueError("window_minutes must be between 1 and 60")
        started = time.perf_counter()
        self.graph.service(incident.tenant_id, service_id)
        run_id = self._run_id()
        payload = self._fixture(incident).get("metrics", {}).get(service_id)
        if payload is None:
            return ToolResult(run_id, "GetServiceMetrics", {"service_id": service_id, "window_minutes": window_minutes}, ToolStatus.NO_DATA, duration_ms=round((time.perf_counter()-started)*1000), limitation="No metrics fixture is available.")
        observation = self._observation(run_id, incident.scenario_id, "metrics", "synthetic-metrics-fixture", service_id, f"Metrics returned for the last {window_minutes} minutes.", payload)
        return ToolResult(run_id, "GetServiceMetrics", {"service_id": service_id, "window_minutes": window_minutes}, ToolStatus.OK, [observation], round((time.perf_counter()-started)*1000))

    def search_logs(self, incident: Incident, service_id: str, query_terms: list[str] | None = None, window_minutes: int = 15) -> ToolResult:
        if not 1 <= window_minutes <= 60:
            raise ValueError("window_minutes must be between 1 and 60")
        started = time.perf_counter()
        self.graph.service(incident.tenant_id, service_id)
        run_id = self._run_id()
        lines = self._fixture(incident).get("logs", {}).get(service_id, [])
        if query_terms:
            query = " ".join(query_terms).lower()
            matching = [line for line in lines if any(token in line.lower() for token in query.split())]
            lines = matching or lines
        if not lines:
            return ToolResult(run_id, "SearchLogs", {"service_id": service_id, "query_terms": query_terms or [], "window_minutes": window_minutes}, ToolStatus.NO_DATA, duration_ms=round((time.perf_counter()-started)*1000), limitation="No matching logs are present; absence is not a health signal.")
        observations = [self._observation(run_id, incident.scenario_id, "log", "synthetic-log-fixture", service_id, line, {"message": line}) for line in lines]
        return ToolResult(run_id, "SearchLogs", {"service_id": service_id, "query_terms": query_terms or [], "window_minutes": window_minutes}, ToolStatus.OK, observations, round((time.perf_counter()-started)*1000))

    def get_recent_deployments(self, incident: Incident, service_id: str, window_minutes: int = 60) -> ToolResult:
        if not 1 <= window_minutes <= 1440:
            raise ValueError("window_minutes must be between 1 and 1440")
        started = time.perf_counter()
        self.graph.service(incident.tenant_id, service_id)
        run_id = self._run_id()
        deployments = [item for item in self._fixture(incident).get("deployments", {}).get(service_id, []) if item["minutes_ago"] <= window_minutes]
        if not deployments:
            return ToolResult(run_id, "GetRecentDeployments", {"service_id": service_id, "window_minutes": window_minutes}, ToolStatus.NO_DATA, duration_ms=round((time.perf_counter()-started)*1000), limitation="No deployment occurred in the bounded window.")
        observations = [self._observation(run_id, incident.scenario_id, "deployment", "synthetic-deployment-fixture", service_id, f"Deployment {item['version']} completed {item['minutes_ago']} minutes ago.", item) for item in deployments]
        return ToolResult(run_id, "GetRecentDeployments", {"service_id": service_id, "window_minutes": window_minutes}, ToolStatus.OK, observations, round((time.perf_counter()-started)*1000))

    def get_dependency_context(self, incident: Incident, service_id: str, max_depth: int = 2) -> ToolResult:
        if not 1 <= max_depth <= 5:
            raise ValueError("max_depth must be between 1 and 5")
        started = time.perf_counter()
        run_id = self._run_id()
        context = self.graph.context(incident.tenant_id, service_id, max_depth)
        payload = {"paths": context["dependency_paths"], "owners": {service.id: service.team for service in context["dependencies"]}}
        observation = self._observation(run_id, incident.scenario_id, "dependency_context", "tenant-graph", service_id, "Bounded service dependency context retrieved.", payload)
        return ToolResult(run_id, "GetDependencyContext", {"service_id": service_id, "max_depth": max_depth}, ToolStatus.OK, [observation], round((time.perf_counter()-started)*1000))
