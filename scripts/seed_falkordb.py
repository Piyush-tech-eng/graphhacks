"""Seed an idempotent named FalkorDB graph for every GraphOps tenant.

Run after ``docker compose up -d``:
    python scripts/seed_falkordb.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from falkordb import FalkorDB

from graphops.fixtures import TENANTS, TOOL_DEFINITIONS


def seed_tenant(client: FalkorDB, tenant_id: str, topology: dict) -> None:
    graph = client.select_graph(f"graphops_{tenant_id}")
    graph.query("MATCH ()-[r]->() DELETE r")
    graph.query("MATCH (n) DELETE n")
    for service_id, service in topology["services"].items():
        graph.query(
            "CREATE (:Service {id:$id, name:$name, team:$team, criticality:$criticality, status:'healthy', dependency_inventory_complete:true})",
            {"id": service_id, "name": service["name"], "team": service["team"], "criticality": service["criticality"]},
        )
    for dependent, dependencies in topology["dependencies"].items():
        for dependency in dependencies:
            graph.query("MATCH (a:Service {id:$a}), (b:Service {id:$b}) CREATE (a)-[:DEPENDS_ON]->(b)", {"a": dependent, "b": dependency})
    for tool in TOOL_DEFINITIONS:
        graph.query("CREATE (:Tool {id:$id, name:$name, risk_tier:$risk, enabled:true})", {"id": tool["id"], "name": tool["name"], "risk": tool["risk"]})
    for from_tool, to_tool, success in [
        ("get_service_health", "get_service_metrics", 0.85),
        ("get_service_metrics", "get_dependency_context", 0.80),
        ("get_service_metrics", "search_logs", 0.90),
        ("search_logs", "plan_recovery", 0.82),
    ]:
        graph.query("MATCH (a:Tool {id:$a}), (b:Tool {id:$b}) CREATE (a)-[:COMMONLY_FOLLOWS {frequency:1, success_rate:$success}]->(b)", {"a": from_tool, "b": to_tool, "success": success})

    def count(query: str) -> int:
        result = graph.query(query)
        return int(result.result_set[0][0])

    expected_dependency_edges = sum(len(items) for items in topology["dependencies"].values())
    checks = {
        "services": (count("MATCH (n:Service) RETURN count(n)"), len(topology["services"])),
        "dependency edges": (count("MATCH (:Service)-[r:DEPENDS_ON]->(:Service) RETURN count(r)"), expected_dependency_edges),
        "tools": (count("MATCH (n:Tool) RETURN count(n)"), len(TOOL_DEFINITIONS)),
        "workflow edges": (count("MATCH (:Tool)-[r:COMMONLY_FOLLOWS]->(:Tool) RETURN count(r)"), 4),
    }
    mismatches = [f"{name}: found {actual}, expected {expected}" for name, (actual, expected) in checks.items() if actual != expected]
    if mismatches:
        raise RuntimeError(f"Invalid FalkorDB seed for {tenant_id}: " + "; ".join(mismatches))


def main() -> None:
    client = FalkorDB(host=os.getenv("FALKORDB_HOST", "localhost"), port=int(os.getenv("FALKORDB_PORT", "6379")))
    for tenant_id, topology in TENANTS.items():
        seed_tenant(client, tenant_id, topology)
        print(f"Seeded graphops_{tenant_id}")


if __name__ == "__main__":
    main()
