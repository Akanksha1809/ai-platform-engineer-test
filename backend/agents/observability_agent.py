"""Watches a deployed rollout and recommends -- but never executes -- a
rollback.

Data sources, all read directly (never inferred by the LLM):
  1. Kubernetes API (via kubectl) -- Rollout status if the service was
     generated after Milestone 5, or Deployment status as a fallback for
     anything deployed before that (e.g. the very first hello-nginx PR).
  2. Prometheus (kube-prometheus-stack) -- pod-level restart counts and
     waiting-reason counts (CrashLoopBackOff / ImagePullBackOff), queried
     via kube-state-metrics, which are the metrics that exist regardless
     of whether the workload is a Rollout or a Deployment (both create
     ordinary Pods underneath).
  3. ArgoCD Application status -- the git commit currently synced, so a
     rollback recommendation can point at a concrete `git revert <sha>`
     instead of a vague "go back a version".

The actual go/no-go decision (`recommended: bool`) is 100% deterministic,
computed from the facts above via fixed thresholds. The LLM (if Ollama is
reachable) is only ever asked to phrase a plain-English summary of facts
it's handed -- never to decide anything or invent a number. This mirrors
docs_agent's LLM-for-narration-only pattern.
"""
from __future__ import annotations

import json
import os
import shutil
from typing import Optional

import requests

from backend.agents.util import ToolExecutionError, run_tool
from backend.llm import ollama_client
from backend.orchestrator.state import (
    MetricResult,
    ObservabilityReport,
    RollbackRecommendation,
    RolloutStatus,
)

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")

# Thresholds for the deterministic recommendation. Deliberately simple and
# named so they're easy to defend/adjust in an interview -- this is meant
# to be legible, not a tuned ML model.
RESTART_COUNT_THRESHOLD = 3


def _kubectl_get_json(args: list[str]) -> Optional[dict]:
    """Runs `kubectl get ... -o json` and returns the parsed object, or
    None if kubectl isn't installed, the resource doesn't exist, or the
    output wasn't valid JSON. Never raises -- every caller treats a None
    as "couldn't determine this," not a crash."""
    if shutil.which("kubectl") is None:
        return None
    try:
        result = run_tool(["kubectl", "get", *args, "-o", "json"], capture_output=True, text=True)
    except ToolExecutionError:
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None


def get_rollout_status(service_name: str, namespace: str) -> RolloutStatus:
    """Tries `kubectl get rollout` first (Argo Rollouts, Milestone 5+);
    falls back to `kubectl get deployment` for anything deployed with the
    older plain-Deployment template, so this agent still works against
    services that predate the canary-strategy switch."""

    obj = _kubectl_get_json(["rollout", service_name, "-n", namespace])
    if obj is not None:
        status = obj.get("status", {})
        spec_steps = obj.get("spec", {}).get("strategy", {}).get("canary", {}).get("steps", [])
        # Kubernetes status objects omit optional int fields entirely when
        # their value is 0 (rather than including an explicit "0") -- e.g.
        # a Rollout with zero available replicas has no "availableReplicas"
        # key in its status JSON at all. .get(key) alone would return None
        # for "the field is absent" and "genuinely zero" alike, and our
        # rollback rule specifically treats None as "unknown, don't flag
        # it" -- so an all-pods-down rollout would silently pass the check.
        # Defaulting to 0 here means "absent" and "zero" are (correctly)
        # treated the same.
        return RolloutStatus(
            ran=True,
            workload_kind="Rollout",
            phase=status.get("phase"),
            replicas=status.get("replicas", 0),
            ready_replicas=status.get("readyReplicas", 0),
            available_replicas=status.get("availableReplicas", 0),
            updated_replicas=status.get("updatedReplicas", 0),
            current_step_index=status.get("currentStepIndex"),
            total_steps=len(spec_steps) or None,
            message=status.get("message", ""),
            output=json.dumps(status, indent=2),
        )

    obj = _kubectl_get_json(["deployment", service_name, "-n", namespace])
    if obj is not None:
        status = obj.get("status", {})
        spec_replicas = obj.get("spec", {}).get("replicas")
        available = status.get("availableReplicas", 0)
        # Deployment has no `.status.phase` field -- synthesize an
        # equivalent so callers can treat both kinds uniformly.
        if spec_replicas is not None and available >= spec_replicas and available > 0:
            phase = "Healthy"
        elif available > 0:
            phase = "Progressing"
        else:
            phase = "Degraded"
        return RolloutStatus(
            ran=True,
            workload_kind="Deployment",
            phase=phase,
            replicas=status.get("replicas", 0),
            ready_replicas=status.get("readyReplicas", 0),
            available_replicas=status.get("availableReplicas", 0),
            updated_replicas=status.get("updatedReplicas", 0),
            message="(plain Deployment -- predates the Argo Rollouts canary template; phase synthesized, not native)",
            output=json.dumps(status, indent=2),
        )

    return RolloutStatus(
        ran=False,
        message=f"Could not find a Rollout or Deployment named '{service_name}' in namespace '{namespace}'. "
        "Is kubectl installed, pointed at the right cluster, and has the service actually been deployed?",
    )


def _query_prometheus(name: str, promql: str) -> MetricResult:
    try:
        resp = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": promql}, timeout=5)
    except requests.RequestException as exc:
        return MetricResult(
            name=name,
            query=promql,
            ran=False,
            output=f"Could not reach Prometheus at {PROMETHEUS_URL} ({exc}). Is "
            "`kubectl port-forward -n monitoring svc/kube-prometheus-stack-prometheus 9090:9090` running?",
        )
    if not resp.ok:
        return MetricResult(name=name, query=promql, ran=False, output=f"Prometheus returned HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    result = data.get("data", {}).get("result", [])
    if not result:
        # A valid, empty result (e.g. zero restarts) is a real answer, not
        # a failure -- report it as 0, not "didn't run".
        return MetricResult(name=name, query=promql, ran=True, value=0.0, output="no matching series (treated as 0)")
    value = float(result[0]["value"][1])
    return MetricResult(name=name, query=promql, ran=True, value=value, output=json.dumps(result))


def get_metrics(service_name: str, namespace: str) -> list[MetricResult]:
    pod_selector = f'namespace="{namespace}",pod=~"{service_name}-.*"'
    queries = {
        "restart_count": f"sum(kube_pod_container_status_restarts_total{{{pod_selector}}})",
        "crashloopbackoff_pods": f'sum(kube_pod_container_status_waiting_reason{{{pod_selector},reason="CrashLoopBackOff"}})',
        "imagepull_error_pods": f'sum(kube_pod_container_status_waiting_reason{{{pod_selector},reason=~"ImagePullBackOff|ErrImagePull"}})',
        "not_ready_pods": f'sum(kube_pod_status_ready{{{pod_selector},condition="false"}})',
    }
    return [_query_prometheus(name, query) for name, query in queries.items()]


def get_argocd_revision(argocd_app_name: str) -> Optional[str]:
    obj = _kubectl_get_json(["application", argocd_app_name, "-n", "argocd"])
    if obj is None:
        return None
    return obj.get("status", {}).get("sync", {}).get("revision")


def _metric(metrics: list[MetricResult], name: str) -> Optional[float]:
    for m in metrics:
        if m.name == name and m.ran:
            return m.value
    return None


def _decide(rollout: RolloutStatus, metrics: list[MetricResult], git_revision: Optional[str]) -> RollbackRecommendation:
    reasons: list[str] = []

    if rollout.ran and rollout.phase == "Degraded":
        reasons.append(f"{rollout.workload_kind} phase is 'Degraded'{': ' + rollout.message if rollout.message else ''}.")

    restart_count = _metric(metrics, "restart_count")
    if restart_count is not None and restart_count > RESTART_COUNT_THRESHOLD:
        reasons.append(f"Pod restart count is {restart_count:.0f}, above the threshold of {RESTART_COUNT_THRESHOLD}.")

    crashloop = _metric(metrics, "crashloopbackoff_pods")
    if crashloop:
        reasons.append(f"{crashloop:.0f} pod(s) are in CrashLoopBackOff.")

    imagepull = _metric(metrics, "imagepull_error_pods")
    if imagepull:
        reasons.append(f"{imagepull:.0f} pod(s) can't pull their image (ImagePullBackOff/ErrImagePull).")

    if (
        rollout.ran
        and rollout.replicas is not None
        and rollout.available_replicas is not None
        and rollout.available_replicas < rollout.replicas
        and rollout.phase != "Progressing"
    ):
        reasons.append(
            f"Only {rollout.available_replicas}/{rollout.replicas} replicas available, and the "
            f"rollout isn't in a normal 'Progressing' state (phase={rollout.phase})."
        )

    recommended = len(reasons) > 0

    if recommended:
        if git_revision:
            suggested_command = (
                f"git revert {git_revision} && git push origin main\n"
                "(ArgoCD's automated self-heal will then sync the cluster back to the previous, "
                "known-good state. Do NOT run `kubectl rollout undo` directly -- self-heal would "
                "just reapply the current Git state over a manual cluster change.)"
            )
        else:
            suggested_command = (
                "Could not determine the currently-synced git revision from ArgoCD. Check "
                "`kubectl get application <name> -n argocd -o jsonpath='{.status.sync.revision}'` "
                "and `git revert <that-sha>` manually."
            )
    else:
        suggested_command = None

    return RollbackRecommendation(
        recommended=recommended,
        reasons=reasons,
        suggested_command=suggested_command,
        git_revision=git_revision,
    )


def _heuristic_summary(service_name: str, rollout: RolloutStatus, recommendation: RollbackRecommendation) -> str:
    if not rollout.ran:
        return f"Could not read rollout status for '{service_name}': {rollout.message}"
    if recommendation.recommended:
        return (
            f"'{service_name}' looks unhealthy ({rollout.phase or 'unknown phase'}, "
            f"{rollout.available_replicas}/{rollout.replicas} replicas available). "
            f"{len(recommendation.reasons)} issue(s) found: {' '.join(recommendation.reasons)} "
            "A human should review the evidence and decide whether to revert."
        )
    return (
        f"'{service_name}' looks healthy: phase={rollout.phase}, "
        f"{rollout.available_replicas}/{rollout.replicas} replicas available, no restart/crash signals found."
    )


def _llm_summary(service_name: str, rollout: RolloutStatus, metrics: list[MetricResult], recommendation: RollbackRecommendation) -> str:
    if ollama_client.is_available():
        facts = {
            "service": service_name,
            "workload_kind": rollout.workload_kind,
            "phase": rollout.phase,
            "replicas": rollout.replicas,
            "available_replicas": rollout.available_replicas,
            "metrics": {m.name: m.value for m in metrics if m.ran},
            "issues_found": recommendation.reasons,
        }
        prompt = (
            "Here are hard facts about a Kubernetes rollout, gathered from the cluster and "
            f"Prometheus (do not invent or alter any of them): {json.dumps(facts)}\n\n"
            "Write a 2-3 sentence plain-English rollout status summary for a human on-call "
            "engineer, suitable as a PR comment or chat message. State whether it looks healthy "
            "or not, and why, using only the facts given. Do not use markdown headers."
        )
        try:
            text = ollama_client.generate(prompt, temperature=0.2).strip()
            if text:
                return text
        except ollama_client.OllamaUnavailable:
            pass
    return _heuristic_summary(service_name, rollout, recommendation)


def run(service_name: str, namespace: str = "default", argocd_app_name: Optional[str] = None) -> ObservabilityReport:
    argocd_app_name = argocd_app_name or service_name

    rollout = get_rollout_status(service_name, namespace)
    metrics = get_metrics(service_name, namespace)
    git_revision = get_argocd_revision(argocd_app_name)

    recommendation = _decide(rollout, metrics, git_revision)
    recommendation.summary = _llm_summary(service_name, rollout, metrics, recommendation)

    notes = []
    if not rollout.ran:
        notes.append("Rollout/Deployment status unavailable -- see rollout_status.message.")
    if not any(m.ran for m in metrics):
        notes.append("No Prometheus metrics were reachable -- is the port-forward to Prometheus running?")
    if git_revision is None:
        notes.append("Could not read ArgoCD Application sync revision -- rollback command is generic, not a specific commit.")

    return ObservabilityReport(
        service_name=service_name,
        namespace=namespace,
        rollout_status=rollout,
        metrics=metrics,
        recommendation=recommendation,
        notes=notes,
    )
