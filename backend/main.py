"""FastAPI entrypoint.

POST /generate {"request": "I need a new public service called orders-api ..."}
returns the full TaskPlan: the parsed spec, every generated file (Terraform +
Helm), and the validation results from each tool that was available to run.

POST /observe {"service_name": "hello-nginx"} checks a deployed service's
rollout health on demand.

POST /webhook/alertmanager is the automatic counterpart -- Alertmanager (in
the kind cluster) calls this when a rollout-related alert fires, and it runs
the same Observability Agent check without a human needing to poll for it.
Every result (automatic or on-demand) is only ever a *recommendation*,
logged for a human to review via GET /recommendations -- nothing here ever
executes a rollback.

Run with:  uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

from backend.agents import observability_agent
from backend.llm import ollama_client
from backend.orchestrator.pipeline import run_pipeline
from backend.orchestrator.state import ObservabilityReport, TaskPlan

app = FastAPI(title="AI Platform Engineer", version="0.1.0")


@app.on_event("startup")
def _warm_up_ollama() -> None:
    """Best-effort: nudge Ollama to load the model into memory now, at
    server startup, rather than on whatever request happens to be first.
    A cold model load is the main cause of the 120s+ timeouts seen during
    real testing -- this doesn't eliminate that cost, just moves it here
    instead of onto a user-facing request. Never blocks startup on failure
    (Ollama not running yet is a completely normal, supported state)."""
    if not ollama_client.is_available(timeout=1.0):
        return
    try:
        ollama_client.generate("Reply with just the word: ready", temperature=0.0)
    except ollama_client.OllamaUnavailable:
        pass

LOG_PATH = Path(__file__).resolve().parents[1] / "logs" / "observability_events.jsonl"

# Deployment/Rollout pod names are generated as <service>-<replicaset-hash>-<pod-hash>,
# e.g. "hello-nginx-694d7bbd8b-cqbmw" -> service "hello-nginx". Best-effort but
# matches Kubernetes' actual, stable naming convention for both workload kinds.
_POD_SUFFIX_RE = re.compile(r"-[a-f0-9]{7,10}-[a-z0-9]{5}$")


def _service_name_from_pod(pod_name: str) -> str:
    return _POD_SUFFIX_RE.sub("", pod_name)


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


def _log_event(triggered_by_alert: Optional[str], report: ObservabilityReport) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "triggered_by_alert": triggered_by_alert,
        "report": report.model_dump(),
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


@app.post("/webhook/alertmanager")
def alertmanager_webhook(payload: dict) -> dict:
    """Alertmanager POSTs its standard webhook payload here when a
    rollout-related alert fires (see infra/monitoring/alertmanager-values.yaml
    for which alerts are routed to this receiver). For each firing alert,
    runs the same deterministic Observability Agent check used by /observe,
    and logs the result -- it does not, and will never, act on it."""
    services_checked = []
    seen: set[tuple[str, str]] = set()

    for alert in payload.get("alerts", []):
        if alert.get("status") != "firing":
            continue
        labels = alert.get("labels", {})
        namespace = labels.get("namespace")
        pod = labels.get("pod")
        if not namespace or not pod:
            continue
        service_name = _service_name_from_pod(pod)
        key = (service_name, namespace)
        if key in seen:
            continue
        seen.add(key)

        report = observability_agent.run(service_name=service_name, namespace=namespace)
        _log_event(triggered_by_alert=labels.get("alertname"), report=report)
        services_checked.append(
            {
                "service_name": service_name,
                "namespace": namespace,
                "triggered_by_alert": labels.get("alertname"),
                "recommended_rollback": report.recommendation.recommended,
            }
        )

    return {"alerts_received": len(payload.get("alerts", [])), "services_checked": services_checked}


@app.get("/recommendations")
def list_recommendations(limit: int = 20) -> list[dict]:
    """Human-facing view of everything the Observability Agent has ever
    recommended (from /observe or the automatic webhook) -- the actual
    approve/revert decision always happens here, by a person reading this,
    never automatically."""
    if not LOG_PATH.exists():
        return []
    lines = LOG_PATH.read_text().strip().splitlines()
    return [json.loads(line) for line in lines[-limit:]]
