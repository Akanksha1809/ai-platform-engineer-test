"""Shared data models passed between agents.

Keeping these as plain Pydantic models (rather than free-form dicts) is what
lets the orchestrator assemble a PR description/cost/security summary
without ever asking the LLM to "remember" structured facts -- every agent
reads and writes the same typed object.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class CloudResource(str, Enum):
    S3_BUCKET = "s3_bucket"
    RDS_POSTGRES = "rds_postgres"
    SQS_QUEUE = "sqs_queue"


class ServiceSpec(BaseModel):
    """The structured intent extracted from a developer's natural-language request."""

    service_name: str = Field(..., description="DNS-safe service name, e.g. 'orders-api'")
    container_image: str = Field(default="nginx:latest")
    container_port: int = Field(default=8080)
    replicas: int = Field(default=2, ge=1, le=20)
    cpu: str = Field(default="250m", description="Kubernetes CPU request, e.g. '250m'")
    memory: str = Field(default="256Mi", description="Kubernetes memory request, e.g. '256Mi'")
    namespace: str = Field(default="default")
    public_endpoint: bool = Field(default=False, description="Expose via internet-facing ALB / Ingress")
    env: dict[str, str] = Field(default_factory=dict)
    cloud_resources: list[CloudResource] = Field(default_factory=list)
    aws_region: str = Field(default="us-east-1")

    model_config = ConfigDict(use_enum_values=True)


class ValidationResult(BaseModel):
    tool: str
    ok: bool
    ran: bool = Field(default=True, description="False if the tool binary wasn't found, so we skipped it")
    output: str = ""


class GeneratedFile(BaseModel):
    path: str
    content: str


class CostEstimate(BaseModel):
    """Output of the Cost Agent (Infracost). Not pass/fail like a
    ValidationResult -- it's a number to surface in the PR, not a gate."""

    tool: str
    ran: bool = True
    monthly_cost_usd: Optional[float] = None
    currency: str = "USD"
    output: str = ""


class PRResult(BaseModel):
    """Output of the GitHub PR client. `attempted=False` means we never even
    tried (no GITHUB_TOKEN/GITHUB_REPO configured), which is a normal,
    demoable state -- not a failure."""

    attempted: bool = False
    created: bool = False
    url: Optional[str] = None
    branch: Optional[str] = None
    message: str = ""


class AgentReport(BaseModel):
    agent: str
    files: list[GeneratedFile] = Field(default_factory=list)
    validations: list[ValidationResult] = Field(default_factory=list)
    cost_estimates: list[CostEstimate] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RolloutStatus(BaseModel):
    """Live status of a deployed workload (Argo Rollouts' Rollout, or a
    plain Deployment as a fallback for anything deployed before Milestone
    5), read directly from the Kubernetes API. These are hard facts --
    never phrased or summarized by the LLM, which only narrates them."""

    ran: bool = True
    workload_kind: Optional[str] = None  # "Rollout" or "Deployment"
    phase: Optional[str] = None  # Rollout: Healthy/Progressing/Degraded/Paused. Deployment: synthesized.
    replicas: Optional[int] = None
    ready_replicas: Optional[int] = None
    available_replicas: Optional[int] = None
    updated_replicas: Optional[int] = None
    current_step_index: Optional[int] = None
    total_steps: Optional[int] = None
    message: str = ""
    output: str = ""  # raw kubectl JSON, kept as evidence


class MetricResult(BaseModel):
    """One PromQL query result against the kube-prometheus-stack Prometheus
    -- also a hard fact, read straight from its HTTP API."""

    name: str
    query: str
    ran: bool = True
    value: Optional[float] = None
    output: str = ""


class RollbackRecommendation(BaseModel):
    """The Observability Agent's output. `recommended` is decided entirely
    by deterministic thresholds against RolloutStatus/MetricResult facts --
    never by the LLM. The LLM (if reachable) only phrases `summary` in
    plain English from those same facts. This agent NEVER executes a
    rollback: `suggested_command` is something a human must run themselves,
    after reviewing the evidence, and it's a `git revert` (not a raw
    `kubectl rollout undo`), since ArgoCD's automated self-heal would just
    reapply the current Git state over any imperative cluster change."""

    recommended: bool = False
    reasons: list[str] = Field(default_factory=list)
    summary: str = ""
    suggested_command: Optional[str] = None
    git_revision: Optional[str] = None


class ObservabilityReport(BaseModel):
    service_name: str
    namespace: str
    rollout_status: RolloutStatus
    metrics: list[MetricResult] = Field(default_factory=list)
    recommendation: RollbackRecommendation
    notes: list[str] = Field(default_factory=list)


class TaskPlan(BaseModel):
    """The end-to-end record of one request, filled in as it moves through the pipeline."""

    request_text: str
    spec: Optional[ServiceSpec] = None
    reports: list[AgentReport] = Field(default_factory=list)
    pr_title: Optional[str] = None
    pr_body: Optional[str] = None
    pr_result: Optional[PRResult] = None

    def all_files(self) -> list[GeneratedFile]:
        return [f for r in self.reports for f in r.files]

    def all_validations(self) -> list[ValidationResult]:
        return [v for r in self.reports for v in r.validations]

    def all_cost_estimates(self) -> list[CostEstimate]:
        return [c for r in self.reports for c in r.cost_estimates]

    def total_monthly_cost_usd(self) -> Optional[float]:
        costs = [c.monthly_cost_usd for c in self.all_cost_estimates() if c.monthly_cost_usd is not None]
        return round(sum(costs), 2) if costs else None

    def ok(self) -> bool:
        return all(v.ok for v in self.all_validations() if v.ran)
