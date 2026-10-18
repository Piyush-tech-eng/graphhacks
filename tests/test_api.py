import sys
import unittest
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from graphops.api import CreateIncidentRequest, create_app
from graphops.engine import GraphOpsEngine
from graphops.trace_store import SQLiteTraceStore


class GraphOpsAPITests(unittest.TestCase):
    def setUp(self):
        self.engine = GraphOpsEngine(trace_store=SQLiteTraceStore(":memory:"))
        self.app = create_app(self.engine)
        self.routes = {route.path: route.endpoint for route in self.app.routes if hasattr(route, "endpoint")}

    def test_dashboard_assets_health_and_scenario_catalog(self):
        self.assertIn("/", self.routes)
        self.assertIn("/styles.css", self.routes)
        self.assertIn("/app.js", self.routes)
        root = self.routes["/"]()
        self.assertTrue(Path(root.path).is_file())
        self.assertEqual("ok", self.routes["/health"]()["status"])
        scenarios = self.routes["/api/scenarios"]()
        self.assertEqual({"acme", "northstar"}, {item["tenant_id"] for item in scenarios})

    def test_incident_lifecycle_preserves_safety_and_persisted_trace(self):
        request = CreateIncidentRequest(scenario_id="db-pool-exhaustion")
        created = self.routes["/api/incidents"](request)
        incident_id = created["id"]
        self.routes["/api/incidents/{incident_id}/step"](incident_id)
        planned = self.routes["/api/incidents/{incident_id}/run"](incident_id)
        plan = planned["action_plan"]
        self.assertEqual("pending_approval", plan["status"])
        self.assertEqual("h-database-pool", plan["hypothesis_id"])

        with self.assertRaises(HTTPException) as blocked:
            self.routes["/api/action-plans/{plan_id}/simulate"](plan["id"])
        self.assertEqual(400, blocked.exception.status_code)
        approved = self.routes["/api/action-plans/{plan_id}/approve"](plan["id"])
        self.assertEqual("approved", approved["action_plan"]["status"])
        simulated = self.routes["/api/action-plans/{plan_id}/simulate"](plan["id"])
        self.assertEqual("simulated_recovery_verified", simulated["status"])
        with self.assertRaises(HTTPException):
            self.routes["/api/action-plans/{plan_id}/simulate"](plan["id"])

        trace = self.routes["/api/incidents/{incident_id}/trace"](incident_id)
        self.assertEqual("incident_created", trace[0]["event_type"])
        self.assertEqual("recovery_simulated", trace[-1]["event_type"])

    def test_api_error_paths_return_client_errors(self):
        with self.assertRaises(HTTPException) as missing_scenario:
            self.routes["/api/incidents"](CreateIncidentRequest(scenario_id="missing"))
        self.assertEqual(404, missing_scenario.exception.status_code)
        with self.assertRaises(ValidationError):
            CreateIncidentRequest()
        with self.assertRaises(HTTPException) as missing_incident:
            self.routes["/api/incidents/{incident_id}"]("unknown")
        self.assertEqual(404, missing_incident.exception.status_code)
        with self.assertRaises(HTTPException) as missing_plan:
            self.routes["/api/action-plans/{plan_id}/approve"]("unknown")
        self.assertEqual(400, missing_plan.exception.status_code)

    def test_benchmark_endpoint_reports_catalogue_fixture_results(self):
        response = self.routes["/api/benchmark"]()
        self.assertEqual(4, response["summary"]["case_count"])
