"""Deterministic evidence ledger and hypothesis quality checks."""

from __future__ import annotations

from datetime import datetime, timezone

from .models import Hypothesis, Incident, Observation


def _matching(observations: list[Observation], service_id: str | None = None, kind: str | None = None, predicate=None) -> list[Observation]:
    matches = [item for item in observations if (service_id is None or item.service_id == service_id) and (kind is None or item.kind == kind)]
    return [item for item in matches if predicate is None or predicate(item)]


def evaluate_hypotheses(incident: Incident) -> list[Hypothesis]:
    """Evaluate only facts stored on this incident; no canned incident conclusions."""
    observations = incident.observations
    target = incident.target_service_id
    db_metric = _matching(observations, "orders-db", "metrics", lambda item: item.values.get("pool_utilization", 0) >= 0.9)
    db_log = _matching(observations, "orders-db", "log", lambda item: "pool" in item.summary.lower() or "connection" in item.summary.lower())
    payment_healthy = _matching(observations, "payments-api", "health", lambda item: item.values.get("state") == "healthy")
    fresh_deploy = _matching(observations, target, "deployment", lambda item: item.values.get("minutes_ago", 10_000) <= 15)
    regression_log = _matching(observations, target, "log", lambda item: "release" in item.summary.lower() or "validation" in item.summary.lower())

    db = Hypothesis(
        id="h-database-pool", statement="A database connection-pool saturation is contributing to the incident.",
        support_ids=[item.id for item in [*db_metric, *db_log]],
        contradiction_ids=[], missing_evidence=["Database wait-event telemetry is unavailable in this fixture."],
    )
    deploy = Hypothesis(
        id="h-recent-deployment", statement="A recent deployment introduced a regression in the alerting service.",
        support_ids=[item.id for item in [*fresh_deploy, *regression_log]], contradiction_ids=[],
        missing_evidence=["A rollback comparison has not been simulated."],
    )
    payment = Hypothesis(
        id="h-payments-outage", statement="The payments dependency is causing checkout failures.",
        support_ids=[], contradiction_ids=[item.id for item in payment_healthy],
        missing_evidence=["No payment failure observation is present."],
    )

    for item in (db, deploy, payment):
        if len(item.support_ids) >= 2 and not item.contradiction_ids:
            item.status, item.confidence_label = "leading", "high"
            item.explanation = "Multiple current-incident observations support this hypothesis."
        elif item.support_ids and not item.contradiction_ids:
            item.status, item.confidence_label = "plausible", "medium"
            item.explanation = "Some evidence supports this hypothesis, but a counter-check remains necessary."
        elif item.contradiction_ids:
            item.status, item.confidence_label = "weakened", "low"
            item.explanation = "A current-incident counter-observation weakens this hypothesis."
        else:
            item.status, item.confidence_label = "inconclusive", "low"
            item.explanation = "No current-incident evidence supports this hypothesis."

    leading = [item for item in (db, deploy, payment) if item.status == "leading"]
    if not leading:
        abstain = Hypothesis(
            id="h-insufficient-evidence", statement="Available evidence is insufficient to distinguish a root cause.",
            missing_evidence=["At least two consistent, incident-scoped observations are required before a recovery plan."],
            status="leading", confidence_label="low",
            explanation="The system abstains rather than inferring a cause from topology alone.",
        )
        return [db, deploy, payment, abstain]
    return [db, deploy, payment]


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def validate_evidence(incident: Incident, hypothesis: Hypothesis, allowed_service_ids: set[str]) -> list[str]:
    """Reject evidence that is absent, stale, out of graph scope, or unchecked."""
    known = {item.id: item for item in incident.observations}
    errors: list[str] = []
    incident_started = _parse_timestamp(incident.created_at)
    for observation_id in [*hypothesis.support_ids, *hypothesis.contradiction_ids]:
        observation = known.get(observation_id)
        if observation is None:
            errors.append(f"Unknown observation id: {observation_id}")
        elif observation.fixture_id != incident.scenario_id:
            errors.append(f"Observation {observation_id} belongs to a different fixture")
        elif observation.service_id not in allowed_service_ids:
            errors.append(f"Observation {observation_id} is outside the incident graph scope")
        else:
            try:
                if _parse_timestamp(observation.timestamp) < incident_started:
                    errors.append(f"Observation {observation_id} predates the incident")
            except ValueError:
                errors.append(f"Observation {observation_id} has an invalid timestamp")

    if hypothesis.status == "leading" and hypothesis.id != "h-insufficient-evidence":
        if len(hypothesis.support_ids) < 2:
            errors.append("A leading root-cause hypothesis needs at least two supporting observations")
        has_counter_check = any(
            observation.kind == "health"
            and observation.service_id != incident.target_service_id
            and observation.values.get("state") == "healthy"
            for observation in incident.observations
        )
        if not has_counter_check:
            errors.append("A leading root-cause hypothesis needs a healthy dependency counter-check")
    return errors
