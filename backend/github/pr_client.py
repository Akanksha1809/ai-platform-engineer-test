"""Opens a GitHub Pull Request containing the generated files.

Uses the GitHub REST API directly (via PyGithub) rather than a local git
clone: branch creation and file commits happen server-side, which is
simpler to run from a stateless backend and needs no git binary or SSH key
on the host.

Configure via environment variables:
    GITHUB_TOKEN         (required) a personal access token with repo scope
    GITHUB_REPO          (required) "owner/repo"
    GITHUB_BASE_BRANCH   (optional, default "main")

If GITHUB_TOKEN or GITHUB_REPO aren't set, this returns a PRResult with
attempted=False instead of raising -- the rest of the pipeline (generated
files, PR title/body) still works without a GitHub account wired up, which
matters for demoing this project without exposing a real token.
"""
from __future__ import annotations

import os
import time

from backend.orchestrator.state import PRResult, TaskPlan

_TOKEN_ENV = "GITHUB_TOKEN"
_REPO_ENV = "GITHUB_REPO"
_BASE_BRANCH_ENV = "GITHUB_BASE_BRANCH"


def open_pull_request(plan: TaskPlan) -> PRResult:
    token = os.environ.get(_TOKEN_ENV)
    repo_name = os.environ.get(_REPO_ENV)

    if not token or not repo_name:
        return PRResult(
            attempted=False,
            message=(
                f"Set {_TOKEN_ENV} and {_REPO_ENV} env vars to actually open a PR against a real "
                "repo. Generated files and the PR title/body are ready in the TaskPlan either way -- "
                "see plan.pr_title / plan.pr_body / plan.all_files()."
            ),
        )

    try:
        from github import Github, GithubException
    except ImportError:
        return PRResult(
            attempted=True,
            created=False,
            message="PyGithub not installed; run `pip install PyGithub` (it's in requirements.txt).",
        )

    base_branch = os.environ.get(_BASE_BRANCH_ENV, "main")
    branch_name = f"ai-platform/{plan.spec.service_name}-{int(time.time())}"

    try:
        gh = Github(token)
        repo = gh.get_repo(repo_name)

        base_ref = repo.get_git_ref(f"heads/{base_branch}")
        repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base_ref.object.sha)

        for f in plan.all_files():
            try:
                existing = repo.get_contents(f.path, ref=branch_name)
                repo.update_file(f.path, f"Update {f.path}", f.content, existing.sha, branch=branch_name)
            except GithubException as exc:
                if exc.status == 404:
                    repo.create_file(f.path, f"Add {f.path}", f.content, branch=branch_name)
                else:
                    raise

        pr = repo.create_pull(
            title=plan.pr_title or f"Add {plan.spec.service_name} service (AI-generated)",
            body=plan.pr_body or "",
            head=branch_name,
            base=base_branch,
        )
        try:
            pr.add_to_labels("ai-generated")
        except GithubException:
            pass  # label may not exist in the target repo yet -- non-fatal

        return PRResult(attempted=True, created=True, url=pr.html_url, branch=branch_name, message="PR opened.")

    except Exception as exc:  # noqa: BLE001 -- report any failure without crashing the pipeline
        return PRResult(attempted=True, created=False, branch=branch_name, message=f"Failed to open PR: {exc}")
