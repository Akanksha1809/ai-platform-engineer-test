"""Estimates the monthly AWS cost of the generated Terraform using Infracost.

Infracost's CLI is free and open-source (Apache-2.0), and it works by
statically parsing the Terraform HCL -- it does NOT need `terraform plan`,
AWS credentials, or a deployed environment to produce an estimate. It does
need a free Infracost API key: run `infracost auth login` once (no credit
card), then create a free organization at dashboard.infracost.io if
prompted -- current versions of the CLI require an organization to exist
even for local/CLI-only use, or you'll see "User has no associated
organization" instead of a cost figure.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from backend.agents.util import ToolExecutionError, run_tool, write_flat
from backend.orchestrator.state import AgentReport, CostEstimate, GeneratedFile


def _extract_monthly_cost(data: dict) -> float | None:
    # Infracost's JSON schema has shifted across CLI versions/commands --
    # try each known shape instead of hard-failing on one.
    #
    # Current (`infracost scan --json`, confirmed against real output):
    #   data["summary"]["total_monthly_cost"] -- a STRING, e.g. "42.470075".
    summary = data.get("summary") or {}
    if summary.get("total_monthly_cost") is not None:
        try:
            return round(float(summary["total_monthly_cost"]), 2)
        except (TypeError, ValueError):
            pass

    # Older (`infracost breakdown --format json`), in case this ever runs
    # against an older infracost install:
    #   data["totalMonthlyCost"], or per-project data["projects"][i]["breakdown"]["totalMonthlyCost"].
    if data.get("totalMonthlyCost") is not None:
        try:
            return round(float(data["totalMonthlyCost"]), 2)
        except (TypeError, ValueError):
            pass

    total = 0.0
    found = False
    for project in data.get("projects") or []:
        cost = (project.get("breakdown") or {}).get("totalMonthlyCost")
        if cost is not None:
            try:
                total += float(cost)
                found = True
            except (TypeError, ValueError):
                continue
    return round(total, 2) if found else None


def run(tf_files: list[GeneratedFile]) -> AgentReport:
    if shutil.which("infracost") is None:
        return AgentReport(
            agent="cost_agent",
            cost_estimates=[
                CostEstimate(
                    tool="infracost",
                    ran=False,
                    output="infracost binary not found on PATH; skipped cost estimate. "
                    "Install from infracost.io/docs, run `infracost auth login` (free, "
                    "no credit card), and re-run.",
                )
            ],
        )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        write_flat(tf_files, tmp_path)

        try:
            # `infracost breakdown` is deprecated in favor of `infracost scan`
            # (same purpose: static cost estimate from Terraform HCL, no
            # `terraform plan`/AWS credentials needed). `scan` takes the path
            # as a plain positional argument (not --path), and JSON output is
            # requested via the global --json flag (not --format json) --
            # confirmed against `infracost scan --help` output directly,
            # since infracost has renamed these more than once.
            result = run_tool(
                ["infracost", "scan", str(tmp_path), "--json"],
                capture_output=True,
                text=True,
            )
        except ToolExecutionError as exc:
            return AgentReport(
                agent="cost_agent",
                cost_estimates=[CostEstimate(tool="infracost", ran=False, output=str(exc))],
            )

        # Deliberately NOT gating on result.returncode here: infracost scan
        # can exit non-zero when FinOps/tagging policies fail (analogous to
        # checkov exiting 1 on failed checks) even though stdout still has a
        # perfectly good, parseable cost breakdown. Always try to parse
        # first; only report "no cost" if that genuinely fails.
        try:
            data = json.loads(result.stdout)
            monthly = _extract_monthly_cost(data)
        except json.JSONDecodeError:
            monthly = None

        return AgentReport(
            agent="cost_agent",
            cost_estimates=[
                CostEstimate(
                    tool="infracost", ran=True, monthly_cost_usd=monthly, output=result.stdout + result.stderr
                )
            ],
        )
