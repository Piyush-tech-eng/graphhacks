import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from graphops.engine import GraphOpsEngine
from graphops.evidence import validate_evidence
from graphops.graph_store import FixtureGraphStore
from graphops.graphroute import GraphRouteSelector
from graphops.trace_store import SQLiteTraceStore


class GraphOpsTests(unittest.TestCase):
    def test_cold_start_uses_incident_and_graph_context_not_transition_history(self):
        engine = GraphOpsEngine()
        incident = engine.create_incident("db-pool-exhaustion")
        selected = GraphRouteSelector(engine.graph).select_next(incident)
        self.assertIsNotNone(selected)
        self.assertEqual("get_service_health", selected.tool_id)
        self.assertEqual("checkout-api", selected.target_service_id)
        self.assertIn("TARGET_UNHEALTHY", selected.reason_codes)

    def test_reverse_blast_radius_finds_direct_and_transitive_dependents(self):
        graph = FixtureGraphStore()
        affected, complete = graph.reverse_dependents("acme", "orders-db")
        self.assertTrue(complete)
        self.assertEqual({"checkout-api", "inventory-api", "frontend"}, {service.id for service in affected})

    def test_tenant_graphs_are_isolated(self):
        graph = FixtureGraphStore()
        self.assertEqual([["portal", "identity-api"]], graph.dependencies("northstar", "portal", max_depth=1))
        with self.assertRaises(KeyError):
            graph.service("northstar", "checkout-api")

    def test_evidence_backed_plan_requires_approval_before_fixture_simulation(self):
        engine = GraphOpsEngine()
        incident = engine.create_incident("db-pool-exhaustion")
        engine.run_to_plan(incident.id)
        before = engine.view(incident.id)
        plan = before["action_plan"]
        self.assertEqual("pending_approval", plan["status"])
        self.assertTrue(plan["simulation_only"])
        self.assertGreaterEqual(len(plan["supporting_observation_ids"]), 2)
        with self.assertRaises(ValueError):
            engine.simulate(plan["id"])
        engine.approve(plan["id"])
        after = engine.simulate(plan["id"])
        self.assertEqual("simulated_recovery_verified", after.status)
        self.assertTrue(after.action_plan.simulation_only)

    def test_unknown_impact_blocks_the_action(self):
        engine = GraphOpsEngine()
        incident = engine.create_incident("incomplete-inventory")
        engine.run_to_plan(incident.id)
        plan = engine.view(incident.id)["action_plan"]
        self.assertEqual("blocked", plan["status"])
        self.assertFalse(plan["impact_complete"])

    def test_inventory_gap_is_scoped_to_its_incident(self):
        engine = GraphOpsEngine()
        blocked = engine.create_incident("incomplete-inventory")
        engine.run_to_plan(blocked.id)
        normal = engine.create_incident("db-pool-exhaustion")
        engine.run_to_plan(normal.id)
        self.assertEqual("blocked", blocked.action_plan.status)
        self.assertTrue(normal.action_plan.impact_complete)
        self.assertEqual("pending_approval", normal.action_plan.status)

    def test_benchmark_reports_measured_fixture_values(self):
        result = GraphOpsEngine().benchmark()
        self.assertEqual(3, result["summary"]["case_count"])
        self.assertEqual(1.0, result["summary"]["graphops_top1_accuracy"])
        self.assertEqual(1.0, result["summary"]["unsafe_action_block_rate"])

    def test_trace_events_are_persisted_to_the_audit_store(self):
        store = SQLiteTraceStore(":memory:")
        engine = GraphOpsEngine(trace_store=store)
        incident = engine.create_incident("db-pool-exhaustion")
        engine.step(incident.id)
        persisted = store.list(incident.id)
        self.assertGreaterEqual(len(persisted), 3)
        self.assertEqual("incident_created", persisted[0]["event_type"])

    def test_evidence_validator_rejects_a_claim_that_cites_no_observation(self):
        engine = GraphOpsEngine()
        incident = engine.create_incident("db-pool-exhaustion")
        engine.run_to_plan(incident.id)
        hypothesis = next(item for item in engine.incident(incident.id).hypotheses if item.id == "h-database-pool")
        hypothesis.support_ids.append("obs-does-not-exist")
        allowed = {"checkout-api", "orders-db", "payments-api", "payments-db"}
        self.assertIn("Unknown observation id: obs-does-not-exist", validate_evidence(incident, hypothesis, allowed))


if __name__ == "__main__":
    unittest.main()
