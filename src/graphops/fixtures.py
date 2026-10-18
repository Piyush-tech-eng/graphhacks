"""Small deterministic, tenant-isolated fixtures used by the safe demo."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


TOOL_DEFINITIONS = [
    {"id": "get_service_health", "name": "GetServiceHealth", "evidence_types": ["health"], "risk": "low", "cost": 0.05},
    {"id": "get_service_metrics", "name": "GetServiceMetrics", "evidence_types": ["metrics"], "risk": "low", "cost": 0.10},
    {"id": "search_logs", "name": "SearchLogs", "evidence_types": ["logs"], "risk": "low", "cost": 0.20},
    {"id": "get_recent_deployments", "name": "GetRecentDeployments", "evidence_types": ["deployments"], "risk": "low", "cost": 0.10},
    {"id": "get_dependency_context", "name": "GetDependencyContext", "evidence_types": ["dependency_context"], "risk": "low", "cost": 0.10},
    {"id": "plan_recovery", "name": "PlanRecovery", "evidence_types": ["recovery_plan"], "risk": "low", "cost": 0.05},
    {"id": "simulate_recovery", "name": "SimulateRecovery", "evidence_types": ["recovery_verification"], "risk": "medium", "cost": 0.30},
]


TENANTS: dict[str, dict[str, Any]] = {
    "acme": {
        "name": "Acme Commerce",
        "services": {
            "frontend": {"name": "Storefront", "team": "Experience", "criticality": 2},
            "checkout-api": {"name": "Checkout API", "team": "Commerce", "criticality": 4},
            "orders-db": {"name": "Orders DB", "team": "Data Platform", "criticality": 5},
            "payments-api": {"name": "Payments API", "team": "Payments", "criticality": 4},
            "payments-db": {"name": "Payments DB", "team": "Payments", "criticality": 4},
            "inventory-api": {"name": "Inventory API", "team": "Fulfilment", "criticality": 3},
        },
        "dependencies": {
            "frontend": ["checkout-api"],
            "checkout-api": ["orders-db", "payments-api"],
            "payments-api": ["payments-db"],
            "inventory-api": ["orders-db"],
            "orders-db": [],
            "payments-db": [],
        },
    },
    "northstar": {
        "name": "Northstar Identity",
        "services": {
            "portal": {"name": "Member Portal", "team": "Experience", "criticality": 2},
            "identity-api": {"name": "Identity API", "team": "Identity", "criticality": 5},
            "user-db": {"name": "User DB", "team": "Identity Data", "criticality": 5},
            "notifications": {"name": "Notification Service", "team": "Engagement", "criticality": 2},
        },
        "dependencies": {
            "portal": ["identity-api"],
            "identity-api": ["user-db"],
            "notifications": ["user-db"],
            "user-db": [],
        },
    },
}


SCENARIOS: dict[str, dict[str, Any]] = {
    "db-pool-exhaustion": {
        "title": "Checkout API HTTP 500 spike",
        "tenant_id": "acme",
        "target_service_id": "checkout-api",
        "severity": "SEV-2",
        "summary": "checkout-api is returning HTTP 500s above the error budget.",
        "truth": "database_pool_exhaustion",
        "initial_status": {"checkout-api": "degraded", "orders-db": "degraded", "payments-api": "healthy"},
        "health": {
            "checkout-api": {"state": "degraded", "http_status": 500},
            "orders-db": {"state": "degraded", "accepting_connections": False},
            "payments-api": {"state": "healthy", "http_status": 200},
        },
        "metrics": {
            "checkout-api": {"error_rate": 0.31, "p95_ms": 2450, "cpu_pct": 42},
            "orders-db": {"pool_utilization": 0.98, "waiting_connections": 47, "cpu_pct": 61},
            "payments-api": {"error_rate": 0.0, "p95_ms": 123},
        },
        "logs": {
            "checkout-api": ["ERROR order lookup failed while acquiring database connection"],
            "orders-db": ["ERROR connection acquisition timeout: pool exhausted"],
            "payments-api": ["INFO health probe succeeded"],
        },
        "deployments": {"checkout-api": [{"version": "2.8.0", "minutes_ago": 300, "status": "successful"}]},
        "plan": {"action_type": "restart_service", "target": "orders-db"},
    },
    "bad-checkout-deployment": {
        "title": "Checkout release regression",
        "tenant_id": "acme",
        "target_service_id": "checkout-api",
        "severity": "SEV-2",
        "summary": "checkout-api errors began immediately after a deployment.",
        "truth": "bad_deployment",
        "initial_status": {"checkout-api": "degraded", "orders-db": "healthy", "payments-api": "healthy"},
        "health": {"checkout-api": {"state": "degraded", "http_status": 500}, "orders-db": {"state": "healthy"}, "payments-api": {"state": "healthy"}},
        "metrics": {"checkout-api": {"error_rate": 0.28, "p95_ms": 1900, "cpu_pct": 44}},
        "logs": {"checkout-api": ["ERROR validation exception: missing tax_region in release 2.8.1"]},
        "deployments": {"checkout-api": [{"version": "2.8.1", "minutes_ago": 4, "status": "successful"}]},
        "plan": {"action_type": "rollback_deployment", "target": "checkout-api"},
    },
    "incomplete-inventory": {
        "title": "Inventory dependency uncertainty",
        "tenant_id": "acme",
        "target_service_id": "inventory-api",
        "severity": "SEV-3",
        "summary": "inventory-api latency is elevated, but the dependency inventory is incomplete.",
        "truth": "insufficient_evidence",
        "inventory_complete": {"orders-db": False},
        "initial_status": {"inventory-api": "degraded", "orders-db": "degraded"},
        "health": {"inventory-api": {"state": "degraded", "http_status": 503}, "orders-db": {"state": "degraded"}},
        "metrics": {"inventory-api": {"error_rate": 0.14, "p95_ms": 3100}, "orders-db": {"pool_utilization": 0.97}},
        "logs": {"inventory-api": ["ERROR downstream query timeout"]},
        "deployments": {},
        "plan": {"action_type": "restart_service", "target": "orders-db"},
    },
}


def scenario_catalog() -> list[dict[str, str]]:
    return [
        {"id": identifier, "title": item["title"], "tenant_id": item["tenant_id"], "summary": item["summary"], "severity": item["severity"]}
        for identifier, item in SCENARIOS.items()
    ]


def get_scenario(scenario_id: str) -> dict[str, Any]:
    if scenario_id not in SCENARIOS:
        raise KeyError(f"Unknown scenario: {scenario_id}")
    return deepcopy(SCENARIOS[scenario_id])


def get_topology(tenant_id: str) -> dict[str, Any]:
    if tenant_id not in TENANTS:
        raise KeyError(f"Unknown tenant: {tenant_id}")
    return deepcopy(TENANTS[tenant_id])
