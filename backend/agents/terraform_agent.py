"""Renders Terraform from vetted Jinja2 templates and validates the result.

Deliberately does NOT ask the LLM to write HCL from scratch. The model only
ever fills in a ServiceSpec (via the planner); this agent is 100%
deterministic template rendering, which is what makes the generated
Terraform reliably valid instead of a coin flip.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from backend.agents.util import ToolExecutionError, run_tool, write_flat
from backend.orchestrator.state import AgentReport, GeneratedFile, ServiceSpec, ValidationResult

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates" / "terraform" / "service"


def render(spec: ServiceSpec) -> list[GeneratedFile]:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    files = []
    for template_name in ("main.tf.j2", "outputs.tf.j2"):
        template = env.get_template(template_name)
        rendered = template.render(spec=spec)
        out_path = f"terraform/{spec.service_name}/{template_name[:-3]}"  # drop .j2
        files.append(GeneratedFile(path=out_path, content=rendered))
    return files


def validate(files: list[GeneratedFile]) -> ValidationResult:
    """Writes the rendered files to a temp dir and runs `terraform validate`.

    Degrades gracefully (ran=False) both when the binary is missing
    entirely (shutil.which pre-check) and when it's found but can't
    actually be executed (ToolExecutionError from run_tool) -- e.g. it was
    installed into a different venv than the one running this server.
    """
    if shutil.which("terraform") is None:
        return ValidationResult(
            tool="terraform validate",
            ok=False,
            ran=False,
            output="terraform binary not found on PATH; skipped validation. "
            "Install Terraform and re-run to validate before opening a PR.",
        )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        write_flat(files, tmp_path)

        try:
            init = run_tool(
                ["terraform", "init", "-backend=false", "-input=false"],
                cwd=tmp_path,
                capture_output=True,
                text=True,
            )
            if init.returncode != 0:
                return ValidationResult(tool="terraform validate", ok=False, output=init.stdout + init.stderr)

            result = run_tool(["terraform", "validate", "-json"], cwd=tmp_path, capture_output=True, text=True)
            return ValidationResult(
                tool="terraform validate",
                ok=result.returncode == 0,
                output=result.stdout + result.stderr,
            )
        except ToolExecutionError as exc:
            return ValidationResult(tool="terraform validate", ok=False, ran=False, output=str(exc))


def run(spec: ServiceSpec) -> AgentReport:
    files = render(spec)
    validation = validate(files)
    return AgentReport(agent="terraform_agent", files=files, validations=[validation])
