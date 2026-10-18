import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from graphops.engine import GraphOpsEngine
from graphops.evidence import validate_evidence
from graphops.fixtures import get_scenario
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
        self.assertEqual(4, result["summary"]["case_count"])
        self.assertEqual(1.0, result["summary"]["graphops_top1_accuracy"])
        self.assertEqual(1.0, result["summary"]["unsafe_action_block_rate"])
        self.assertIn("not an independently executed selector", result["measurement_note"])

    def test_northstar_scenario_exercises_separate_tenant_graph(self):
        engine = GraphOpsEngine()
        incident = engine.create_incident("northstar-identity-deployment")
        engine.run_to_plan(incident.id)
        self.assertEqual("northstar", incident.tenant_id)
        self.assertEqual("h-recent-deployment", incident.action_plan.hypothesis_id)
        self.assertEqual("identity-api", incident.action_plan.target_service_id)
        self.assertTrue(all(item["passed"] for item in incident.action_plan.preconditions))

    def test_incident_graph_view_includes_selected_target_and_action_blast_radius(self):
        engine = GraphOpsEngine()
        incident = engine.create_incident("db-pool-exhaustion")
        engine.run_to_plan(incident.id)
        graph = engine.view(incident.id)["graph_context"]
        node_ids = {node["id"] for node in graph["nodes"]}
        roles = {node["id"]: node["roles"] for node in graph["nodes"]}
        self.assertEqual(incident.selections[-1].target_service_id, graph["selected_target_service_id"])
        self.assertIn("incident_target", roles["checkout-api"])
        self.assertIn("action_target", roles["orders-db"])
        self.assertTrue(set(graph["blast_radius_service_ids"]).issubset(node_ids))
        self.assertTrue(any(edge["source"] == "checkout-api" and edge["target"] == "orders-db" for edge in graph["edges"]))

    def test_action_safety_rejects_a_target_outside_alert_graph(self):
        engine = GraphOpsEngine()
        incident = engine.create_incident("bad-checkout-deployment")
        engine.run_to_plan(incident.id)
        fixture = get_scenario(incident.scenario_id)
        fixture["plan"]["target"] = "ghost-service"
        # Alter fixture plan metadata and ensure the gate rejects a target outside
        # the graph path supported by the incident evidence.
        from graphops.safety import SafetyGate
        with patch("graphops.safety.get_scenario", return_value=fixture):
            proposed = SafetyGate(engine.graph).propose(incident)
        self.assertFalse(next(item for item in proposed.preconditions if item["name"] == "target is in incident dependency scope")["passed"])
        self.assertFalse(next(item for item in proposed.preconditions if item["name"] == "action target matches diagnostic evidence")["passed"])

    def test_file_backed_trace_connections_close_after_operations(self):
        original_connect = sqlite3.connect
        connections = []

        def tracked_connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            with patch("graphops.trace_store.sqlite3.connect", side_effect=tracked_connect):
                store = SQLiteTraceStore(str(Path(directory) / "audit.sqlite3"))
                store.append("incident-1", {"sequence": 1, "event_type": "created", "created_at": "now", "payload": {}})
                self.assertEqual(1, len(store.list("incident-1")))

        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

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
