"""Deterministic rank-only scale evaluation for GraphRoute's score formula."""

from __future__ import annotations

import random
import re
from statistics import mean
from time import perf_counter_ns
from typing import Any

from .fixtures import TOOL_DEFINITIONS
from .graphroute import GraphRouteSelector, TRANSITION_PRIOR, TOOLS


CATALOG_SIZES = (20, 60, 150, 300)
CALL_BUDGET = 1
TIMING_REPEATS = 40

TOOL_PROFILES: dict[str, dict[str, Any]] = {
    "get_service_health": {"description": "Read current health status and availability for a service", "scopes": ("target", "upstream")},
    "get_service_metrics": {"description": "Inspect service latency error rate and database pool metrics", "scopes": ("target", "upstream")},
    "get_dependency_context": {"description": "Traverse the service dependency graph and inspect connected dependencies", "scopes": ("graph",)},
    "get_recent_deployments": {"description": "Inspect recent deployment release and change history", "scopes": ("target",)},
    "search_logs": {"description": "Search service logs for errors exceptions and release validation failures", "scopes": ("target", "upstream")},
    "plan_recovery": {"description": "Build an evidence-linked recovery proposal and impact review", "scopes": ("target",)},
    "simulate_recovery": {"description": "Verify a fixture-only recovery simulation", "scopes": ("target",)},
}

TASKS: tuple[dict[str, str], ...] = (
    {"id": "target-health", "query": "Check the alerting service health and availability", "expected_tool": "get_service_health", "evidence": "health", "scope": "target", "last_tool": ""},
    {"id": "dependency-metrics", "query": "Inspect database connection pool saturation metrics on an upstream dependency", "expected_tool": "get_service_metrics", "evidence": "metrics", "scope": "upstream", "last_tool": "get_dependency_context"},
    {"id": "dependency-countercheck", "query": "Verify health of a connected database dependency as a counter check", "expected_tool": "get_service_health", "evidence": "health", "scope": "upstream", "last_tool": "get_dependency_context"},
    {"id": "deployment-window", "query": "Inspect recent deployment release change history for the incident", "expected_tool": "get_recent_deployments", "evidence": "deployments", "scope": "target", "last_tool": "get_service_health"},
    {"id": "release-log-check", "query": "Search alerting service logs for release validation exceptions", "expected_tool": "search_logs", "evidence": "logs", "scope": "target", "last_tool": "get_recent_deployments"},
    {"id": "dependency-topology", "query": "Traverse connected service dependencies and graph context", "expected_tool": "get_dependency_context", "evidence": "dependency_context", "scope": "graph", "last_tool": "get_service_health"},
)

DISTRACTOR_DESCRIPTIONS = (
    "Read a recent service status snapshot",
    "Inspect general latency and error telemetry",
    "Search operational records for a change or exception",
    "List connected resources and dependency metadata",
    "Review historical deployment and release notes",
    "Collect a diagnostic signal from a related service",
    "Summarize application events for investigation",
    "Inspect a resource health and metrics overview",
)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _lexical_relevance(query: str, candidate_text: str) -> float:
    query_tokens = _tokens(query)
    candidate_tokens = _tokens(candidate_text)
    if not query_tokens or not candidate_tokens:
        return 0.0
    return len(query_tokens & candidate_tokens) / len(query_tokens | candidate_tokens)


def _catalog(size: int) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for definition in TOOL_DEFINITIONS:
        profile = TOOL_PROFILES[definition["id"]]
        tool = TOOLS[definition["id"]]
        catalog.append({
            "id": tool.id,
            "name": tool.name,
            "description": profile["description"],
            "evidence_types": tool.evidence_types,
            "scopes": profile["scopes"],
            "cost": tool.cost,
            "risk": 0.65 if tool.risk_tier.value == "medium" else 0.0,
            "synthetic_distractor": False,
        })
    rng = random.Random(7242026)
    lure_phrases = [task["query"] for task in TASKS]
    while len(catalog) < size:
        index = len(catalog) - len(TOOL_DEFINITIONS)
        description = rng.choice(DISTRACTOR_DESCRIPTIONS)
        if index % 4 == 0:
            description = f"{description}; related context: {rng.choice(lure_phrases)}"
        evidence = rng.choice(("health", "metrics", "logs", "deployments", "dependency_context"))
        catalog.append({
            "id": f"synthetic_probe_{index:03d}",
            "name": f"SyntheticProbe{index:03d}",
            "description": description,
            "evidence_types": (evidence,),
            "scopes": (),
            "cost": round(rng.uniform(0.2, 1.0), 3),
            "risk": rng.choice((0.0, 0.0, 0.65)),
            "synthetic_distractor": True,
        })
    return catalog


def _graphroute_rank(task: dict[str, str], catalog: list[dict[str, Any]]) -> tuple[str, int]:
    ranked: list[tuple[float, str]] = []
    for item in catalog:
        relevance = _lexical_relevance(task["query"], f"{item['name']} {item['description']}")
        expected_evidence = task["evidence"] in item["evidence_types"]
        gap_value = 0.96 if expected_evidence else (0.025 if item["synthetic_distractor"] else 0.08)
        if item["synthetic_distractor"]:
            graph_context = 0.06
        elif task["scope"] in item["scopes"]:
            graph_context = 0.95
        else:
            graph_context = 0.35
        history = TRANSITION_PRIOR.get((task["last_tool"], item["id"]), 0.50)
        score = GraphRouteSelector.score_features(relevance, graph_context, gap_value, history, item["cost"], item["risk"])
        ranked.append((score, item["id"]))
    # The same lexical id tie-break is applied by the production GraphRoute ranker.
    return sorted(ranked, key=lambda item: (-item[0], item[1]))[0][1], len(catalog)


def _flat_baseline_rank(task: dict[str, str], catalog: list[dict[str, Any]]) -> tuple[str, int]:
    ranked = [
        (_lexical_relevance(task["query"], f"{item['name']} {item['description']}"), item["id"])
        for item in catalog
    ]
    return sorted(ranked, key=lambda item: (-item[0], item[1]))[0][1], len(catalog)


def evaluate_catalog_scale() -> dict[str, Any]:
    """Compare the actual GraphRoute score function and an independent lexical baseline.

    Both methods rank the exact same deterministic candidate catalog for each task,
    return one top choice, and share a one-call budget. Catalog fillers are explicitly
    synthetic decoys; this is rank-only, offline method evaluation.
    """
    results: list[dict[str, Any]] = []
    for size in CATALOG_SIZES:
        catalog = _catalog(size)
        graphroute_hits = 0
        baseline_hits = 0
        graphroute_times: list[float] = []
        baseline_times: list[float] = []
        graphroute_evaluations: list[int] = []
        baseline_evaluations: list[int] = []
        per_task: list[dict[str, Any]] = []
        for task in TASKS:
            graphroute_id, evaluated = _graphroute_rank(task, catalog)
            baseline_id, baseline_evaluated = _flat_baseline_rank(task, catalog)
            graphroute_correct = graphroute_id == task["expected_tool"]
            baseline_correct = baseline_id == task["expected_tool"]
            graphroute_hits += int(graphroute_correct)
            baseline_hits += int(baseline_correct)
            graphroute_evaluations.append(evaluated)
            baseline_evaluations.append(baseline_evaluated)
            per_task.append({
                "task_id": task["id"], "expected_tool": task["expected_tool"],
                "graphroute_tool": graphroute_id, "graphroute_correct": graphroute_correct,
                "baseline_tool": baseline_id, "baseline_correct": baseline_correct,
                "calls_used": 1,
            })
            for _ in range(TIMING_REPEATS):
                started = perf_counter_ns()
                _graphroute_rank(task, catalog)
                graphroute_times.append((perf_counter_ns() - started) / 1000)
                started = perf_counter_ns()
                _flat_baseline_rank(task, catalog)
                baseline_times.append((perf_counter_ns() - started) / 1000)
        count = len(TASKS)
        results.append({
            "catalog_size": size,
            "case_count": count,
            "graphroute": {
                "top1_accuracy_percent": round(100 * graphroute_hits / count, 1),
                "correct_calls_within_budget_percent": round(100 * graphroute_hits / count, 1),
                "mean_candidate_evaluations": round(mean(graphroute_evaluations), 1),
                "mean_selection_us": round(mean(graphroute_times), 2),
            },
            "baseline": {
                "top1_accuracy_percent": round(100 * baseline_hits / count, 1),
                "correct_calls_within_budget_percent": round(100 * baseline_hits / count, 1),
                "mean_candidate_evaluations": round(mean(baseline_evaluations), 1),
                "mean_selection_us": round(mean(baseline_times), 2),
            },
            "tasks": per_task,
        })
    return {
        "task_count": len(TASKS),
        "call_budget": CALL_BUDGET,
        "scoring_description": "GraphRoute weighted relevance/graph/evidence/history score versus lexical Jaccard alert-only ranking",
        "measurement_note": "Deterministic, offline synthetic rank-only evaluation. Each method sees the same catalog and task and may return one tool choice. Results are not production accuracy, a general-LLM comparison, or a real tool-execution benchmark.",
        "methodology": "Six authored diagnostic-routing tasks, seven real tool metadata profiles, and seeded synthetic distractor tools at catalog sizes 20, 60, 150, and 300. The GraphRoute side reuses the production score weights (task relevance, graph fit, evidence-gap value, transition prior, cost, and risk) across the full candidate catalog. The independent flat baseline ranks the same candidates only by query/name/description token Jaccard overlap. Both have a top-1 decision and one diagnostic-call budget per task. Catalog generation is seeded; timing is local Python ranking time averaged over repeated runs and is host-dependent. No APIs or infrastructure are called.",
        "catalog_scale": results,
    }
