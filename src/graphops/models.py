from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RiskTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolStatus(str, Enum):
    OK = "ok"
    NO_DATA = "no_data"
    BLOCKED = "blocked"
    ERROR = "error"


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    team: str
    criticality: int
    status: str = "healthy"
    dependency_inventory_complete: bool = True


@dataclass(frozen=True)
class ToolDefinition:
    id: str
    name: str
    evidence_types: tuple[str, ...]
    risk_tier: RiskTier
    cost: float
    enabled: bool = True


@dataclass
class Observation:
    id: str
    kind: str
    source: str
    service_id: str
    summary: str
    values: dict[str, Any]
    timestamp: str = field(default_factory=now_iso)
    fixture_id: str = ""
    tool_run_id: str = ""


@dataclass
class ToolResult:
    tool_run_id: str
    tool_name: str
    arguments: dict[str, Any]
    status: ToolStatus
    observations: list[Observation] = field(default_factory=list)
    duration_ms: int = 0
    limitation: str | None = None


@dataclass
class Selection:
    tool_id: str
    target_service_id: str
    rank: int
    score: float
    score_components: dict[str, float]
    reason_codes: list[str]
    graph_facts_used: list[str]
    expected_evidence: str
    risk_tier: RiskTier
    historical_success_source: str = "static transition prior"


@dataclass
class TraceEvent:
    sequence: int
    event_type: str
    created_at: str
    payload: dict[str, Any]


@dataclass
class Hypothesis:
    id: str
    statement: str
    support_ids: list[str] = field(default_factory=list)
    contradiction_ids: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    status: str = "inconclusive"
    confidence_label: str = "low"
    explanation: str = "Not yet evaluated."


@dataclass
class ActionPlan:
    id: str
    incident_id: str
    action_type: str
    target_service_id: str
    risk_tier: RiskTier
    reason: str
    supporting_observation_ids: list[str]
    blast_radius: list[dict[str, Any]]
    impact_complete: bool
    preconditions: list[dict[str, Any]]
    hypothesis_id: str | None = None
    action_source: str = "synthetic scenario fixture"
    status: str = "pending_approval"
    simulation_only: bool = True
    verification_observation_id: str | None = None
    blocked_reason: str | None = None


@dataclass
class Incident:
    id: str
    scenario_id: str
    tenant_id: str
    target_service_id: str
    summary: str
    severity: str
    created_at: str = field(default_factory=now_iso)
    status: str = "investigating"
    last_tool_id: str | None = None
    resolved_gaps: set[str] = field(default_factory=set)
    observations: list[Observation] = field(default_factory=list)
    selections: list[Selection] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    action_plan: ActionPlan | None = None
    trace: list[TraceEvent] = field(default_factory=list)


def plain(value: Any) -> Any:
    """Make domain objects safe to expose through the HTTP API."""
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return plain(asdict(value))
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [plain(item) for item in value]
    return value
