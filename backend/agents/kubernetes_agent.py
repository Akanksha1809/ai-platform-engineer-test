"""Renders a Helm chart from vetted templates and validates it.

Chart.yaml and values.yaml are Jinja2 templates (filled in from the
ServiceSpec). The files under templates/ use Helm's own Go-template syntax
({{ .Values.x }}) and are copied through verbatim -- they are NOT run
through Jinja, since both engines use the same {{ }} delimiter and mixing
them would corrupt the Helm templates. Helm renders those itself at
`helm template` / `helm install` time.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader

from backend.agents.util import ToolExecutionError, run_tool, write_tree
from backend.orchestrator.state import AgentReport, GeneratedFile, ServiceSpec, ValidationResult

CHART_TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates" / "helm" / "service"

# Files rendered through Jinja2 (contain {{ spec.* }} placeholders only).
_JINJA_FILES = ["Chart.yaml.j2", "values.yaml.j2"]

# Files copied verbatim (contain Helm's own {{ .Values.* }} template syntax).
_STATIC_FILES = [
    "templates/deployment.yaml",
    "templates/service.yaml",
    "templates/_helpers.tpl",
]


def render(spec: ServiceSpec) -> list[GeneratedFile]:
    env = Environment(
        loader=FileSystemLoader(str(CHART_TEMPLATE_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    chart_dir = f"helm/{spec.service_name}"
    files: list[GeneratedFile] = []

    for name in _JINJA_FILES:
        rendered = env.get_template(name).render(spec=spec)
        out_name = name[:-3]  # drop .j2
        files.append(GeneratedFile(path=f"{chart_dir}/{out_name}", content=rendered))

    for rel_path in _STATIC_FILES:
        content = (CHART_TEMPLATE_DIR / rel_path).read_text()
        files.append(GeneratedFile(path=f"{chart_dir}/{rel_path}", content=content))

    return files


def render_manifest(files: list[GeneratedFile]) -> Optional[str]:
    """Runs `helm template` on the given chart files and returns the
    flattened Kubernetes manifest YAML, or None if helm isn't installed or
    couldn't actually be executed. Shared by validate() here and by
    security_agent, which scans this same rendered manifest instead of
    re-running helm itself."""
    if shutil.which("helm") is None:
        return None

    with tempfile.TemporaryDirectory() as tmp:
        chart_dir = write_tree(files, Path(tmp))
        try:
            result = run_tool(["helm", "template", str(chart_dir)], capture_output=True, text=True)
        except ToolExecutionError:
            return None
        if result.returncode != 0:
            return None
        return result.stdout


def validate(files: list[GeneratedFile]) -> list[ValidationResult]:
    results: list[ValidationResult] = []

    if shutil.which("helm") is None:
        results.append(
            ValidationResult(
                tool="helm template",
                ok=False,
                ran=False,
                output="helm binary not found on PATH; skipped validation. "
                "Install Helm and re-run to validate before opening a PR.",
            )
        )
        return results

    with tempfile.TemporaryDirectory() as tmp:
        chart_dir = write_tree(files, Path(tmp))
        try:
            result = run_tool(["helm", "template", str(chart_dir)], capture_output=True, text=True)
        except ToolExecutionError as exc:
            results.append(ValidationResult(tool="helm template", ok=False, ran=False, output=str(exc)))
            results.append(ValidationResult(tool="kubeconform", ok=False, ran=False, output="skipped -- helm template didn't run"))
            return results

        rendered_manifest = result.stdout
        results.append(
            ValidationResult(tool="helm template", ok=result.returncode == 0, output=result.stdout + result.stderr)
        )

        if result.returncode == 0 and shutil.which("kubeconform") is not None:
            try:
                kc = run_tool(
                    ["kubeconform", "-strict", "-summary"],
                    input=rendered_manifest,
                    capture_output=True,
                    text=True,
                )
                results.append(
                    ValidationResult(tool="kubeconform", ok=kc.returncode == 0, output=kc.stdout + kc.stderr)
                )
            except ToolExecutionError as exc:
                results.append(ValidationResult(tool="kubeconform", ok=False, ran=False, output=str(exc)))
        elif result.returncode == 0:
            results.append(
                ValidationResult(
                    tool="kubeconform",
                    ok=False,
                    ran=False,
                    output="kubeconform binary not found on PATH; skipped schema validation.",
                )
            )

    return results


def run(spec: ServiceSpec) -> AgentReport:
    files = render(spec)
    validations = validate(files)
    return AgentReport(agent="kubernetes_agent", files=files, validations=validations)
