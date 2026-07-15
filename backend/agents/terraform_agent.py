"""Renders Terraform from vetted Jinja2 templates and validates the result.

Deliberately does NOT ask the LLM to write HCL from scratch. The model only
ever fills in a ServiceSpec (via the planner); this agent is 100%
deterministic template rendering, which is what makes the generated
Terraform reliably valid instead of a coin flip.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from backend.agents.util import ToolExecutionError, run_tool, write_flat
from backend.orchestrator.state import AgentReport, GeneratedFile, ServiceSpec, ValidationResult

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates" / "terraform" / "service"

# Every validate() call runs `terraform init` in a fresh temp directory, so
# without a plugin cache, EVERY request re-downloads the AWS provider
# (~100+MB) from registry.terraform.io from scratch. Pointing all of them at
# one persistent, shared cache directory means only the very first call ever
# has to actually download it -- every call after that reuses the cached
# binary, which is both faster and far less exposed to registry hiccups
# (like the transient timeout seen during real testing).
PLUGIN_CACHE_DIR = Path(__file__).resolve().parents[2] / ".terraform-cache" / "plugin-cache"

# A transient registry blip (DNS hiccup, momentary timeout) shouldn't take
# down the whole validation -- retry `terraform init` once after a short
# pause before giving up and reporting a real failure.
INIT_RETRY_DELAY_SECONDS = 3


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

    PLUGIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TF_PLUGIN_CACHE_DIR": str(PLUGIN_CACHE_DIR)}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        write_flat(files, tmp_path)

        try:
            init = run_tool(
                ["terraform", "init", "-backend=false", "-input=false"],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                env=env,
            )
            if init.returncode != 0:
                # Could be a real config problem, or a transient registry
                # blip (network timeout resolving provider versions) -- retry
                # once before treating it as a genuine failure. A real config
                # error will fail identically both times, at no extra cost
                # beyond one short delay; a transient one usually clears up.
                time.sleep(INIT_RETRY_DELAY_SECONDS)
                init = run_tool(
                    ["terraform", "init", "-backend=false", "-input=false"],
                    cwd=tmp_path,
                    capture_output=True,
                    text=True,
                    env=env,
                )
                if init.returncode != 0:
                    return ValidationResult(tool="terraform validate", ok=False, output=init.stdout + init.stderr)

            result = run_tool(
                ["terraform", "validate", "-json"], cwd=tmp_path, capture_output=True, text=True, env=env
            )
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
