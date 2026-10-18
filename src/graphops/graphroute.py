"""Cold-start-safe, interpretable GraphRoute selection policy."""

from __future__ import annotations

from dataclasses import dataclass

from .fixtures import TOOL_DEFINITIONS
from .graph_store import FixtureGraphStore
from .models import Incident, RiskTier, Selection, ToolDefinition


TOOLS = {
    item["id"]: ToolDefinition(
        id=item["id"], name=item["name"], evidence_types=tuple(item["evidence_types"]),
        risk_tier=RiskTier(item["risk"]), cost=item["cost"], enabled=True,
    )
    for item in TOOL_DEFINITIONS
}


TRANSITION_PRIOR: dict[tuple[str, str], float] = {
    ("get_service_health", "get_service_metrics"): 0.85,
    ("get_service_metrics", "get_dependency_context"): 0.80,
    ("get_dependency_context", "get_service_metrics"): 0.72,
    ("get_service_metrics", "search_logs"): 0.90,
    ("search_logs", "get_service_health"): 0.65,
    ("get_service_health", "get_recent_deployments"): 0.60,
    ("get_recent_deployments", "plan_recovery"): 0.70,
    ("search_logs", "plan_recovery"): 0.82,
}


@dataclass(frozen=True)
class Candidate:
    tool_id: str
    target_service_id: str
    evidence_gap: str
    reason_codes: tuple[str, ...]
    graph_facts: tuple[str, ...]
    task_relevance: float
    graph_context: float
    gap_value: float


class GraphRouteSelector:
    """Ranks concrete tool calls; graph transitions are a signal, never a gate."""

    def __init__(self, graph: FixtureGraphStore) -> None:
        self.graph = graph

    @staticmethod
    def score_features(task_relevance: float, graph_context: float, evidence_gap: float, history: float, cost: float, risk: float) -> float:
        """Shared, auditable score formula used by routing and scale evaluation."""
        return (
            0.28 * task_relevance
            + 0.22 * graph_context
            + 0.32 * evidence_gap
            + 0.12 * history
            - 0.04 * cost
            - 0.02 * risk
        )

    @staticmethod
    def _completed(incident: Incident, gap: str, target: str) -> bool:
        return f"{gap}:{target}" in incident.resolved_gaps

    @staticmethod
    def _first_dependency(incident: Incident, graph: FixtureGraphStore) -> str | None:
        paths = graph.dependencies(incident.tenant_id, incident.target_service_id, max_depth=1)
        return paths[0][-1] if paths else None

    @staticmethod
    def _alternate_dependency(incident: Incident, graph: FixtureGraphStore) -> str | None:
        paths = graph.dependencies(incident.tenant_id, incident.target_service_id, max_depth=1)
        return paths[1][-1] if len(paths) > 1 else None

    def _candidates(self, incident: Incident) -> list[Candidate]:
        target = incident.target_service_id
        primary_dependency = self._first_dependency(incident, self.graph)
        alternate_dependency = self._alternate_dependency(incident, self.graph)
        facts = tuple(self.graph.graph_facts(incident.tenant_id, target, max_depth=2))
        candidates: list[Candidate] = []

        def add(tool_id: str, candidate_target: str, gap: str, reasons: tuple[str, ...], relevance: float, context: float, value: float) -> None:
            if not self._completed(incident, gap, candidate_target):
                candidates.append(Candidate(tool_id, candidate_target, gap, reasons, facts, relevance, context, value))

        # The first three candidates form the cold-start path. They do not depend on
        # a prior tool or a learned graph edge.
        add("get_service_health", target, "health", ("TARGET_UNHEALTHY", "HEALTH_NOT_CHECKED"), 0.98, 0.60, 0.96)
        add("get_service_metrics", target, "metrics", ("TARGET_UNHEALTHY", "METRICS_NOT_CHECKED"), 0.94, 0.60, 0.91)
        add("get_dependency_context", target, "dependency_context", ("DEPENDENCIES_UNCHECKED",), 0.72, 0.95, 0.72)
        add("get_recent_deployments", target, "deployments", ("CHANGE_WINDOW_UNCHECKED",), 0.70, 0.42, 0.66)

        # Graph context changes the next target: inspect a direct dependency before
        # treating the alerting service itself as the only possible cause.
        if self._completed(incident, "dependency_context", target) and primary_dependency:
            add("get_service_metrics", primary_dependency, "metrics", ("DEPENDENCY_METRICS_UNCHECKED",), 0.88, 0.96, 0.94)
            has_healthy_dependency = any(
                observation.kind == "health"
                and observation.service_id != target
                and observation.values.get("state") == "healthy"
                for observation in incident.observations
            )
            if not has_healthy_dependency:
                add("get_service_health", primary_dependency, "health", ("DEPENDENCY_COUNTER_CHECK_REQUIRED",), 0.84, 0.92, 0.90)
            if self._completed(incident, "metrics", primary_dependency):
                add("search_logs", primary_dependency, "logs", ("DEPENDENCY_LOGS_UNCHECKED",), 0.92, 0.97, 0.96)

        # A normal sibling dependency is a deliberate counter-check, not a claim of
        # causation from the graph edge alone.
        if self._completed(incident, "logs", primary_dependency or target) and alternate_dependency:
            add("get_service_health", alternate_dependency, "health", ("COUNTER_CHECK_REQUIRED",), 0.84, 0.88, 0.84)

        fresh_target_deployment = any(
            observation.kind == "deployment"
            and observation.service_id == target
            and observation.values.get("minutes_ago", 10_000) <= 15
            for observation in incident.observations
        )
        # A temporal correlation is not enough to label a deployment causal.  A
        # matching target log is required before a rollback can be proposed.
        if fresh_target_deployment:
            add("search_logs", target, "logs", ("DEPLOYMENT_COUNTERCHECK_REQUIRED",), 0.98, 0.82, 0.99)

        diagnostics_done = (
            self._completed(incident, "health", target)
            and self._completed(incident, "metrics", target)
            and self._completed(incident, "dependency_context", target)
            and self._completed(incident, "deployments", target)
            and (not primary_dependency or self._completed(incident, "metrics", primary_dependency))
            and (not primary_dependency or self._completed(incident, "logs", primary_dependency))
            and (not fresh_target_deployment or self._completed(incident, "logs", target))
        )
        if diagnostics_done:
            add("plan_recovery", target, "recovery_plan", ("EVIDENCE_REVIEW_READY",), 0.96, 0.82, 0.98)

        return candidates

    def rank(self, incident: Incident) -> list[Selection]:
        candidates = self._candidates(incident)
        ranked: list[Selection] = []
        for candidate in candidates:
            tool = TOOLS[candidate.tool_id]
            if not tool.enabled or tool.risk_tier == RiskTier.HIGH:
                continue
            default_history = TRANSITION_PRIOR.get((incident.last_tool_id or "", tool.id), 0.50)
            history, history_source = self.graph.transition_prior_details(incident.tenant_id, incident.last_tool_id, tool.id, default_history)
            risk = {RiskTier.LOW: 0.0, RiskTier.MEDIUM: 0.65, RiskTier.HIGH: 1.0}[tool.risk_tier]
            score = self.score_features(
                candidate.task_relevance, candidate.graph_context, candidate.gap_value,
                history, tool.cost, risk,
            )
            ranked.append(Selection(
                tool_id=tool.id,
                target_service_id=candidate.target_service_id,
                rank=0,
                score=round(score, 4),
                score_components={
                    "task_relevance": candidate.task_relevance,
                    "graph_context": candidate.graph_context,
                    "evidence_gap": candidate.gap_value,
                    "historical_success": history,
                    "estimated_cost": tool.cost,
                    "risk_penalty": risk,
                },
                reason_codes=list(candidate.reason_codes),
                graph_facts_used=list(candidate.graph_facts),
                expected_evidence=", ".join(tool.evidence_types),
                risk_tier=tool.risk_tier,
                historical_success_source=history_source,
            ))
        ranked.sort(key=lambda item: (-item.score, item.tool_id, item.target_service_id))
        for index, selection in enumerate(ranked, start=1):
            selection.rank = index
        return ranked

    def select_next(self, incident: Incident) -> Selection | None:
        candidates = self.rank(incident)
        return candidates[0] if candidates else None
