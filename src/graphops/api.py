from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .engine import GraphOpsEngine


class CreateIncidentRequest(BaseModel):
    scenario_id: str


def create_app(engine: GraphOpsEngine | None = None) -> FastAPI:
    app = FastAPI(title="GraphOps", version="0.1.0", description="Synthetic incident investigation and simulation only.")
    app.state.engine = engine or GraphOpsEngine()
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    # The dashboard is deliberately usable as a local static preview too.  Keeping
    # these asset paths relative lets ``static/index.html`` render correctly when
    # opened from disk, while these routes make the exact same document work at
    # the application root.
    @app.get("/styles.css", include_in_schema=False)
    def dashboard_styles() -> FileResponse:
        return FileResponse(static_dir / "styles.css", media_type="text/css")

    @app.get("/app.js", include_in_schema=False)
    def dashboard_script() -> FileResponse:
        return FileResponse(static_dir / "app.js", media_type="text/javascript")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "graph": app.state.engine.graph.health(), "safety": "diagnostics are fixture-backed; remediation is simulation only"}

    @app.get("/api/scenarios")
    def scenarios() -> list[dict]:
        return app.state.engine.scenarios()

    @app.post("/api/incidents")
    def create_incident(request: CreateIncidentRequest) -> dict:
        try:
            incident = app.state.engine.create_incident(request.scenario_id)
            return app.state.engine.view(incident.id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/incidents/{incident_id}/step")
    def step(incident_id: str) -> dict:
        try:
            return app.state.engine.step(incident_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/incidents/{incident_id}/run")
    def run(incident_id: str) -> dict:
        try:
            app.state.engine.run_to_plan(incident_id)
            return app.state.engine.view(incident_id)
        except (KeyError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/incidents/{incident_id}")
    def get_incident(incident_id: str) -> dict:
        try:
            return app.state.engine.view(incident_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/incidents/{incident_id}/trace")
    def trace(incident_id: str) -> list[dict]:
        events = app.state.engine.persisted_trace(incident_id)
        if not events:
            raise HTTPException(status_code=404, detail=f"Unknown incident trace: {incident_id}")
        return events

    @app.post("/api/action-plans/{plan_id}/approve")
    def approve(plan_id: str) -> dict:
        try:
            incident = app.state.engine.approve(plan_id)
            return app.state.engine.view(incident.id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/action-plans/{plan_id}/simulate")
    def simulate(plan_id: str) -> dict:
        try:
            incident = app.state.engine.simulate(plan_id)
            return app.state.engine.view(incident.id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/benchmark")
    def benchmark() -> dict:
        return app.state.engine.benchmark()

    return app


app = create_app()
