"""The orchestrator: dispatches a request through the agent pipeline.

Milestone 3 scope: planner -> terraform_agent -> kubernetes_agent ->
security_agent -> cost_agent -> docs_agent -> GitHub PR. CI/CD, GitOps, and
observability agents plug into this same TaskPlan object in later
milestones -- see docs/architecture.md for the full pipeline.
"""
from __future__ import annotations

from backend.agents import cost_agent, docs_agent, kubernetes_agent, security_agent, terraform_agent
from backend.github import pr_client
from backend.orchestrator import planner
from backend.orchestrator.state import AgentReport, TaskPlan


def run_pipeline(request_text: str, open_pr: bool = True) -> TaskPlan:
    plan_obj = TaskPlan(request_text=request_text)

    spec, planner_notes = planner.plan(request_text)
    plan_obj.spec = spec
    plan_obj.reports.append(AgentReport(agent="planner", notes=planner_notes))

    tf_report = terraform_agent.run(spec)
    plan_obj.reports.append(tf_report)

    k8s_report = kubernetes_agent.run(spec)
    plan_obj.reports.append(k8s_report)

    plan_obj.reports.append(security_agent.run(tf_report.files, k8s_report.files))
    plan_obj.reports.append(cost_agent.run(tf_report.files))

    docs_agent.run(plan_obj)  # fills in plan_obj.pr_title / plan_obj.pr_body

    if open_pr:
        plan_obj.pr_result = pr_client.open_pull_request(plan_obj)

    return plan_obj
