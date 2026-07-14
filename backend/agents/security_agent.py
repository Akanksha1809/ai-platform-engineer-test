"""Runs security + policy validation against the generated Terraform and
Kubernetes files.

Two deliberately different layers, since they solve different problems:
- Checkov: broad, pre-built ruleset (~1000 checks) across Terraform and
  Kubernetes -- the "did we do something obviously wrong" pass.
- OPA/Conftest: our own Rego policies (policies/opa/kubernetes.rego) --
  org-specific rules we own and can change instantly, which is what
  actually demonstrates policy-as-code rather than "ran a linter."

Both degrade gracefully (ValidationResult.ran=False) if the binary isn't
installed OR if it's on PATH but can't actually be executed -- matching
terraform_agent / kubernetes_agent. Checkov/Conftest finding issues is a
normal, expected outcome (ok=False, ran=True) -- it means the scanner
worked, not that something crashed.

Checkov specifically is invoked as `sys.executable <path-to-checkov-script>`
rather than a bare `"checkov"` command. Reason: checkov is a pip-installed
Python script with a `#!/path/to/python3` shebang line, and if the project
lives under a directory with a space in its name (e.g. "AI Platform
Assistant"), the OS's shebang parser -- which naively splits on the first
space -- truncates that interpreter path and fails with "bad interpreter:
... no such file or directory". Explicitly handing the script to Python as
an argument (instead of letting the OS exec the script file directly)
skips shebang parsing entirely, so this works regardless of the project
path. Terraform/Helm/kubeconform/Conftest/Infracost are all compiled Go
binaries with no shebang line, so they're unaffected by this class of bug.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from backend.agents.kubernetes_agent import render_manifest
from backend.agents.util import ToolExecutionError, run_tool, write_flat
from backend.orchestrator.state import AgentReport, GeneratedFile, ValidationResult

POLICY_DIR = Path(__file__).resolve().parents[2] / "policies" / "opa"


def _checkov_terraform(tf_files: list[GeneratedFile]) -> ValidationResult:
    checkov_path = shutil.which("checkov")
    if checkov_path is None:
        return ValidationResult(
            tool="checkov (terraform)",
            ok=False,
            ran=False,
            output="checkov binary not found on PATH; skipped. `pip install checkov` (free/OSS) and re-run.",
        )
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        write_flat(tf_files, tmp_path)
        try:
            result = run_tool(
                [sys.executable, checkov_path, "-d", str(tmp_path), "--framework", "terraform", "--compact", "--quiet"],
                capture_output=True,
                text=True,
            )
        except ToolExecutionError as exc:
            return ValidationResult(tool="checkov (terraform)", ok=False, ran=False, output=str(exc))
        # checkov exits 1 when it finds failed checks -- that's a normal
        # "scan ran and found issues" result, not a crash.
        return ValidationResult(
            tool="checkov (terraform)", ok=result.returncode == 0, output=result.stdout + result.stderr
        )


def _checkov_kubernetes(manifest_text: str) -> ValidationResult:
    checkov_path = shutil.which("checkov")
    if checkov_path is None:
        return ValidationResult(
            tool="checkov (kubernetes)",
            ok=False,
            ran=False,
            output="checkov binary not found on PATH; skipped. `pip install checkov` (free/OSS) and re-run.",
        )
    with tempfile.TemporaryDirectory() as tmp:
        manifest_path = Path(tmp) / "manifest.yaml"
        manifest_path.write_text(manifest_text)
        try:
            result = run_tool(
                [sys.executable, checkov_path, "-f", str(manifest_path), "--framework", "kubernetes", "--compact", "--quiet"],
                capture_output=True,
                text=True,
            )
        except ToolExecutionError as exc:
            return ValidationResult(tool="checkov (kubernetes)", ok=False, ran=False, output=str(exc))
        return ValidationResult(
            tool="checkov (kubernetes)", ok=result.returncode == 0, output=result.stdout + result.stderr
        )


def _conftest_kubernetes(manifest_text: str) -> ValidationResult:
    if shutil.which("conftest") is None:
        return ValidationResult(
            tool="conftest",
            ok=False,
            ran=False,
            output="conftest binary not found on PATH; skipped. Install from "
            "conftest.dev (free/OSS) and re-run to enforce policies/opa/kubernetes.rego.",
        )
    with tempfile.TemporaryDirectory() as tmp:
        manifest_path = Path(tmp) / "manifest.yaml"
        manifest_path.write_text(manifest_text)
        try:
            result = run_tool(
                ["conftest", "test", str(manifest_path), "--policy", str(POLICY_DIR)],
                capture_output=True,
                text=True,
            )
        except ToolExecutionError as exc:
            return ValidationResult(tool="conftest", ok=False, ran=False, output=str(exc))
        return ValidationResult(tool="conftest", ok=result.returncode == 0, output=result.stdout + result.stderr)


def run(tf_files: list[GeneratedFile], k8s_files: list[GeneratedFile]) -> AgentReport:
    validations = [_checkov_terraform(tf_files)]

    manifest = render_manifest(k8s_files)
    if manifest:
        validations.append(_checkov_kubernetes(manifest))
        validations.append(_conftest_kubernetes(manifest))
    else:
        skip_reason = "helm binary not found (or couldn't be executed) on PATH; couldn't render the manifest to scan it."
        validations.append(ValidationResult(tool="checkov (kubernetes)", ok=False, ran=False, output=skip_reason))
        validations.append(ValidationResult(tool="conftest", ok=False, ran=False, output=skip_reason))

    return AgentReport(agent="security_agent", validations=validations)
