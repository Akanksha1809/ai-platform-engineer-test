"""FastAPI entrypoint.

POST /generate {"request": "I need a new public service called orders-api ..."}
returns the full TaskPlan: the parsed spec, every generated file (Terraform +
Helm), and the validation results from each tool that was available to run.

Run with:  uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from typing import Optional

from backend.agents import observability_agent
from backend.orchestrator.pipeline import run_pipeline
from backend.orchestrator.state import ObservabilityReport, TaskPlan

app = FastAPI(title="AI Platform Engineer", version="0.1.0")


class GenerateRequest(BaseModel):
    request: str
    open_pr: bool = True  # set false to validate/generate only, no GitHub PR


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/generate", response_model=TaskPlan)
def generate(body: GenerateRequest) -> TaskPlan:
    return run_pipeline(body.request, open_pr=body.open_pr)


class ObserveRequest(BaseModel):
    service_name: str
    namespace: str = "default"
    argocd_app_name: Optional[str] = None  # defaults to service_name if not given


@app.post("/observe", response_model=ObservabilityReport)
def observe(body: ObserveRequest) -> ObservabilityReport:
    """Milestone 5: checks a deployed service's rollout health (Kubernetes
    API + Prometheus) and returns a report with -- if warranted -- a
    rollback recommendation. Never executes anything; a human decides."""
    return observability_agent.run(
        service_name=body.service_name,
        namespace=body.namespace,
        argocd_app_name=body.argocd_app_name,
    )
