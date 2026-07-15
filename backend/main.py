"""FastAPI entrypoint.

POST /generate {"request": "I need a new public service called orders-api ..."}
returns the full TaskPlan: the parsed spec, every generated file (Terraform +
Helm), and the validation results from each tool that was available to run.

Run with:  uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from backend.orchestrator.pipeline import run_pipeline
from backend.orchestrator.state import TaskPlan

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
