"""Opt-in tests for the real FalkorDB adapter.

Run after starting Docker Compose and seeding both tenant graphs with:
    docker compose up -d
    python scripts/seed_falkordb.py
    GRAPHOPS_RUN_FALKORDB_TESTS=1 python -m pytest tests/test_falkordb_integration.py -q
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from graphops.engine import GraphOpsEngine
from graphops.graph_store import FalkorDBGraphStore


@unittest.skipUnless(os.getenv("GRAPHOPS_RUN_FALKORDB_TESTS") == "1", "Set GRAPHOPS_RUN_FALKORDB_TESTS=1 after starting and seeding FalkorDB")
class FalkorDBIntegrationTests(unittest.TestCase):
    def test_seeded_tenant_graphs_isolate_and_persist_runtime_records(self):
        graph = FalkorDBGraphStore(os.getenv("FALKORDB_HOST", "localhost"), int(os.getenv("FALKORDB_PORT", "6379")))
        self.assertTrue(graph.health()["connected"], graph.health()["detail"])
        engine = GraphOpsEngine(graph=graph)

        acme = engine.create_incident("db-pool-exhaustion")
        engine.step(acme.id)
        engine.run_to_plan(acme.id)
        self.assertEqual("acme", acme.tenant_id)
        self.assertTrue(engine.graph.dependencies("acme", "checkout-api", max_depth=2))
        with self.assertRaises(KeyError):
            engine.graph.service("northstar", "checkout-api")

        northstar = engine.create_incident("northstar-identity-deployment")
        engine.run_to_plan(northstar.id)
        self.assertEqual("northstar", northstar.tenant_id)
        self.assertEqual("pending_approval", northstar.action_plan.status)
