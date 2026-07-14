"""One-off local test: opens a REAL GitHub pull request using pr_client,
without needing the FastAPI server running at all. This is the fastest way
to confirm branch-create -> file-commit -> PR-open works against your own
repo before you trust the full pipeline with it.

Before running this:
  1. Create a scratch GitHub repo (with a README, so it has a commit on main).
  2. Create a fine-grained PAT scoped to that repo (Contents + Pull requests: Read and write).
  3. In this same terminal, set:
       export GITHUB_TOKEN="your-token"
       export GITHUB_REPO="your-username/your-repo"
  (Full instructions: docs/testing-github-pr.md)

Run from the project root:
    python3 scripts/test_pr_client.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Let this script be run directly (`python3 scripts/test_pr_client.py`)
# without needing the project installed as a package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.github import pr_client
from backend.orchestrator.state import AgentReport, GeneratedFile, ServiceSpec, TaskPlan


def main() -> None:
    spec = ServiceSpec(service_name="pr-client-smoke-test")

    plan = TaskPlan(request_text="test request: verify pr_client can open a real PR")
    plan.spec = spec
    plan.reports.append(
        AgentReport(
            agent="test",
            files=[
                GeneratedFile(
                    path="ai-platform-engineer-test.txt",
                    content="Hello from ai-platform-engineer! This file was committed by scripts/test_pr_client.py.\n",
                )
            ],
        )
    )
    plan.pr_title = "Test: ai-platform-engineer PR automation"
    plan.pr_body = (
        "This is an automated test PR opened by `scripts/test_pr_client.py` to verify that the "
        "GitHub PR client (branch creation, file commit, PR open) works end-to-end against a real "
        "repository.\n\nSafe to close and delete this PR/branch once confirmed."
    )

    print("Calling pr_client.open_pull_request() ...\n")
    result = pr_client.open_pull_request(plan)

    print("attempted:", result.attempted)
    print("created:  ", result.created)
    print("url:      ", result.url)
    print("branch:   ", result.branch)
    print("message:  ", result.message)

    if result.created:
        print(f"\nSuccess! Open the PR here: {result.url}")
    elif not result.attempted:
        print("\nGITHUB_TOKEN / GITHUB_REPO aren't set. See docs/testing-github-pr.md for setup steps.")
    else:
        print("\nSomething went wrong opening the PR -- see the message above for why.")


if __name__ == "__main__":
    main()
