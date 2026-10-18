"""Fail-closed blast-radius and simulated-remediation gates."""

from __future__ import annotations

import uuid

from .fixtures import get_scenario
from .graph_store import FixtureGraphStore
from .models import ActionPlan, Incident, Observation, RiskTier


class SafetyGate:
    def __init__(self, graph: FixtureGraphStore) -> None:
        self.graph = graph

    def propose(self, incident: Incident) -> ActionPlan:
        fixture = get_scenario(incident.scenario_id)
        configured = fixture["plan"]
        target = configured["target"]
        action_type = configured["action_type"]
        in_scope_targets = {incident.target_service_id}
        in_scope_targets.update(path[-1] for path in self.graph.dependencies(incident.tenant_id, incident.target_service_id, max_depth=5))
        target_in_scope = target in in_scope_targets
        if target_in_scope:
            dependents, complete = self.graph.reverse_dependents(incident.tenant_id, target)
        else:
            dependents, complete = [], False
        # Scenario inventory flags are a property of this incident, not mutable
        # graph-store state.  Otherwise an incomplete-inventory incident could
        # leak its fail-closed flag into a later, unrelated investigation.
        declared_inventory_complete = all(fixture.get("inventory_complete", {}).values())
        complete = complete and declared_inventory_complete
        leading = next((item for item in incident.hypotheses if item.status == "leading" and item.id != "h-insufficient-evidence"), None)
        action_matches_hypothesis = bool(
            leading
            and (
                (action_type == "restart_service" and leading.id == "h-database-pool")
                or (action_type == "rollback_deployment" and leading.id == "h-recent-deployment")
            )
        )
        target_matches_diagnosis = bool(
            leading
            and (
                (leading.id == "h-database-pool" and any(item.service_id == target for item in incident.observations if item.id in leading.support_ids))
                or (leading.id == "h-recent-deployment" and target == incident.target_service_id)
            )
        )
        preconditions = [
            {"name": "known action and target", "passed": action_type in {"restart_service", "rollback_deployment"}},
            {"name": "target is in incident dependency scope", "passed": target_in_scope},
            {"name": "evidence-linked leading hypothesis", "passed": leading is not None},
            {"name": "action type matches the leading hypothesis", "passed": action_matches_hypothesis},
            {"name": "action target matches diagnostic evidence", "passed": target_matches_diagnosis},
            {"name": "dependency inventory complete", "passed": complete},
            {"name": "simulation only", "passed": True},
        ]
        failed = [item["name"] for item in preconditions if not item["passed"]]
        plan = ActionPlan(
            id=f"plan-{uuid.uuid4().hex[:10]}", incident_id=incident.id, action_type=action_type,
            target_service_id=target, risk_tier=RiskTier.MEDIUM,
            reason=leading.statement if leading else "No evidence-linked recovery recommendation is available.",
            supporting_observation_ids=leading.support_ids if leading else [],
            blast_radius=[{"service_id": item.id, "service_name": item.name, "team": item.team, "criticality": item.criticality} for item in dependents],
            impact_complete=complete, preconditions=preconditions,
            hypothesis_id=leading.id if leading else None,
            status="blocked" if failed else "pending_approval",
            blocked_reason=("; ".join(failed) if failed else None),
        )
        return plan

    def approve(self, plan: ActionPlan) -> ActionPlan:
        if plan.status == "blocked":
            raise ValueError(f"Blocked action plans cannot be approved: {plan.blocked_reason}")
        if plan.status != "pending_approval":
            raise ValueError(f"Only pending plans can be approved, current status is {plan.status}")
        plan.status = "approved"
        return plan

    def simulate(self, incident: Incident) -> Observation:
        plan = incident.action_plan
        if plan is None or plan.status != "approved":
            raise ValueError("An explicitly approved action plan is required before simulation")
        observation = Observation(
            id=f"obs-{uuid.uuid4().hex[:10]}", kind="recovery_verification", source="fixture-simulator",
            service_id=plan.target_service_id,
            summary=f"Simulated {plan.action_type} completed; no external system was changed.",
            values={"simulation_only": True, "previous_state": "degraded", "new_state": "healthy", "verification": "passed"},
            fixture_id=incident.scenario_id,
        )
        plan.status = "simulated"
        plan.verification_observation_id = observation.id
        return observation
