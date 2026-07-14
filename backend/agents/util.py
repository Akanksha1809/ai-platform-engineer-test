"""Shared helpers for writing GeneratedFile lists to disk before shelling
out to an external CLI (terraform, helm, checkov, conftest, infracost all
need real files on a real filesystem -- none of them accept in-memory
strings)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from backend.orchestrator.state import GeneratedFile


def write_flat(files: list[GeneratedFile], root: Path) -> None:
    """Writes every file directly into `root`, ignoring subdirectories in
    `path` -- used for Terraform, where all files just need to sit together
    in one directory for `terraform validate` / `infracost breakdown`."""
    for f in files:
        dest = root / Path(f.path).name
        dest.write_text(f.content)


def write_tree(files: list[GeneratedFile], root: Path) -> Path:
    """Recreates each file's relative directory structure under `root` --
    used for Helm charts, where the templates/ subdirectory matters.
    Returns the directory two levels deep (e.g. helm/<service_name>)."""
    chart_dir = None
    for f in files:
        parts = Path(f.path).parts  # e.g. ("helm", "my-service", "Chart.yaml")
        chart_dir = root / parts[0] / parts[1]
        dest = root / Path(f.path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f.content)
    return chart_dir


class ToolExecutionError(RuntimeError):
    """A tool was found by shutil.which() but couldn't actually be
    executed -- most commonly because it's installed in a different Python
    environment/venv (or a different shell's PATH) than the one this
    server process inherited. Every agent catches this and degrades to a
    `ran=False` result instead of letting it crash the whole request with
    a 500, the same way a missing binary already degrades gracefully."""


def run_tool(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run() that converts a last-second FileNotFoundError into
    a typed, catchable exception instead of an opaque crash. shutil.which()
    checks are a fast pre-check for a clear "not installed" message; this
    is the safety net for when that check and reality disagree."""
    try:
        return subprocess.run(cmd, **kwargs)
    except FileNotFoundError as exc:
        raise ToolExecutionError(
            f"'{cmd[0]}' was found on PATH but could not be executed ({exc}). It's likely "
            f"installed in a different Python environment/venv than the one running this "
            f"server -- reinstall it with this venv active, then restart the server."
        ) from exc
