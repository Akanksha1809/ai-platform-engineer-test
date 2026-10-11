# AI Platform Engineer (Milestone 5)

Turns a natural-language infra request into a validated, cost-estimated, security-scanned GitHub Pull Request with an AI-written explanation; once merged, Jenkins CI-validates it and ArgoCD deploys it (as an Argo Rollouts canary) to a local Kubernetes cluster; and once deployed, an Observability Agent watches the rollout via Prometheus + the Rollout's own status and recommends -- but never executes -- a rollback if something's wrong. Uses a locally-hosted LLM (Ollama) and zero paid services except AWS itself, which is never auto-applied (see docs/architecture.md section 5). This is Milestones 1-5 of the full pipeline; only the AWS demo cluster (Milestone 6) remains.

## Pipeline (current scope)

```
NL request
  -> planner            (Ollama, or heuristic fallback)         -> ServiceSpec
  -> terraform_agent      renders + `terraform validate`
  -> kubernetes_agent      renders Helm chart + `helm template` / `kubeconform`
  -> security_agent        checkov (terraform + kubernetes) + conftest (our Rego policies)
  -> cost_agent             infracost breakdown -> monthly cost estimate
  -> docs_agent              assembles PR title/body (LLM explanation + templated facts)
  -> pr_client                 opens the GitHub PR via the REST API (PyGithub)
```

Every agent reads/writes the same `TaskPlan` object (`backend/orchestrator/state.py`). Numbers and pass/fail results in the PR body are always templated in directly from the structured `AgentReport`s -- the LLM is only ever asked for a short, constrained "why" paragraph, never to restate a cost figure or scan result from memory.

## What's new in Milestone 3

- **Docs Agent** (`backend/agents/docs_agent.py`): builds the PR title and a markdown body with a summary, the list of generated files, every validation result, the cost estimate, and a rollback-plan note. The summary paragraph comes from Ollama if it's reachable (with a strict "don't invent facts" instruction), or a templated sentence if it isn't.
- **GitHub PR client** (`backend/github/pr_client.py`): uses PyGithub against the GitHub REST API (no local git clone needed) to create a branch, commit every generated file, and open the PR with the Docs Agent's title/body. Configured via `GITHUB_TOKEN` + `GITHUB_REPO` env vars.
- If those env vars aren't set, `pr_client` returns `attempted: false` instead of failing -- the full pipeline (generation, validation, security, cost, PR text) still runs and is demoable without a GitHub account wired up. This mirrors the `ran: false` pattern used for terraform/helm/checkov/conftest/infracost.

## Setup

```bash
# 1. Python deps
pip install -r requirements.txt

# 2. (Optional but recommended) local LLM for real NL understanding + PR explanations
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &
ollama pull qwen2.5-coder:3b   # or qwen2.5-coder:7b / 14b for better quality on higher-RAM machines

# 3. (Optional) validation tools -- generation/scanning work without them,
#    results just show "SKIPPED" until installed.
#    Terraform:   https://developer.hashicorp.com/terraform/install
#    Helm:        https://helm.sh/docs/intro/install/
#    kubeconform: https://github.com/yannh/kubeconform#installation
#    Checkov:     pip install checkov
#    Conftest:    https://www.conftest.dev/install/
#    Infracost:   https://www.infracost.io/docs/#quick-start, then `infracost auth login` (free)

# 4. (Optional) GitHub PR automation -- a fine-grained PAT with
#    Contents + Pull requests read/write on the target repo is enough.
export GITHUB_TOKEN="ghp_..."
export GITHUB_REPO="your-org/your-repo"
export GITHUB_BASE_BRANCH="main"   # optional, defaults to "main"
```

New to this? A full click-by-click walkthrough (creating the test repo, the token, everything) is in `docs/testing-github-pr.md`, and `scripts/test_pr_client.py` lets you test just the PR step without running the whole server.

Want to prove the *entire* pipeline works with real tools instead of graceful skips (real Ollama planning, real terraform/helm/checkov/conftest/infracost)? Follow `docs/verify-first-half.md` -- it installs everything step by step and tells you exactly what a successful run looks like. `scripts/check_tools.sh` shows at a glance what's already installed.

## Run

```bash
uvicorn backend.main:app --reload --port 8000
```

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"request": "I need a new public service called orders-api using image myorg/orders-api:1.4.0 on port 8080 with 3 replicas, needs an s3 bucket and a postgres database"}'
```

The response's `pr_title` / `pr_body` fields are exactly what gets posted to GitHub, so you can inspect them even without `GITHUB_TOKEN` set. `pr_result` tells you whether a PR was actually opened (`attempted`, `created`, `url`, `branch`, or a `message` explaining why not).

## Verified

Smoke-tested end-to-end in this dev environment (heuristic-parser path; Ollama/Terraform/Helm/Checkov/Conftest/Infracost/PyGithub are all unavailable in this sandbox, so every external call exercised its graceful-skip path):

- The full `orders-api` request produced a correctly-formed PR title and a markdown body with an accurate summary sentence, the right 7 generated files listed, all 5 validation results shown as skipped with clear reasons, and the cost section correctly reporting "skipped."
- Caught and fixed a real bug during this pass: the cost/cloud-resource summary was rendering as `CloudResource.S3_BUCKET` instead of `s3_bucket`, because Python's `Enum.__str__` prints `ClassName.MEMBER` even for `str`-subclassed enums (confirmed with a standalone interpreter check, not assumed). Fixed with a small `_resource_name()` helper in `docs_agent.py` that reads `.value` directly, so the PR body is correct regardless of exactly how pydantic ends up storing the enum.
- Verified `pr_client.open_pull_request()` takes the right branch in both realistic no-token states: no `GITHUB_TOKEN`/`GITHUB_REPO` set (`attempted: false`, pipeline still completes) and token+repo set without PyGithub installed (`attempted: true, created: false`, clear message). Could not exercise an actual GitHub API call here -- no network access to github.com in this sandbox -- so the branch-create/file-commit/PR-open sequence itself is still unverified against a real repo. Test that next with a real token against a scratch repo before relying on it.

## Repo layout

```
backend/
├── main.py
├── orchestrator/
│   ├── state.py                 # ServiceSpec / TaskPlan / ValidationResult / CostEstimate / PRResult
│   ├── planner.py
│   └── pipeline.py               # wires all six stages together
├── agents/
│   ├── terraform_agent.py
│   ├── kubernetes_agent.py        # also exposes render_manifest() for security_agent
│   ├── security_agent.py          # checkov + conftest
│   ├── cost_agent.py              # infracost
│   ├── docs_agent.py               # PR title/body assembly
│   └── util.py                     # shared write_flat/write_tree helpers
├── github/
│   └── pr_client.py                 # PyGithub REST API calls
└── llm/
    └── ollama_client.py
templates/
├── terraform/service/
└── helm/service/
policies/
└── opa/kubernetes.rego
infra/
├── kind/kind-config.yaml        # local Kubernetes cluster definition
├── argocd/applicationset.yaml    # auto-discovers helm/<service>/ folders
├── jenkins/
│   ├── Dockerfile                 # Jenkins + terraform/helm/kubectl baked in
│   └── docker-compose.yml
└── monitoring/
    └── alertmanager-values.yaml   # routes rollout-failure alerts to our webhook
Jenkinsfile                        # validates on merge; does not deploy
```

`backend/agents/observability_agent.py` (Milestone 5) reads Rollout/Deployment status via `kubectl` and metrics via Prometheus's HTTP API; `backend/orchestrator/state.py` holds its `RolloutStatus` / `MetricResult` / `RollbackRecommendation` / `ObservabilityReport` models.

## Milestone 4: CI/CD + GitOps

Once a PR is merged to `main`:

- **Jenkins** (`Jenkinsfile`, self-hosted locally via `infra/jenkins/`) validates every generated `terraform/<service>/` (via `terraform validate`) and `helm/<service>/` (via `helm lint`) folder. It does **not** run `terraform apply` or deploy anything -- see the comment block at the top of `Jenkinsfile` for why.
- **ArgoCD** (`infra/argocd/applicationset.yaml`), running in a local `kind` cluster (`infra/kind/kind-config.yaml`), watches the repo directly and auto-deploys every `helm/<service>/` folder it finds -- this is what actually ships the Kubernetes side of a generated service, independent of Jenkins.

Full setup and the merge-to-running-pod walkthrough: **`docs/milestone4-cicd-gitops.md`**.

## Milestone 5: Observability + rollback recommendations

Once ArgoCD deploys a service, the Observability Agent can check its health on demand or automatically:

- **Argo Rollouts canary strategy**: the Helm chart's workload template (`templates/helm/service/templates/rollout.yaml`) renders an `argoproj.io/v1alpha1 Rollout` instead of a plain Deployment, with a basic (replica-weighted, no service-mesh dependency) canary: 50% weight, a 30s pause, then auto-promote. `kubeconform` validates it against the real Argo Rollouts CRD schema (via the `datreeio/CRDs-catalog` project), not just core Kubernetes kinds.
- **kube-prometheus-stack** (`infra/monitoring/alertmanager-values.yaml` holds the Alertmanager routing override): Prometheus + Grafana + Alertmanager + kube-state-metrics, installed via the standard Helm chart into the same `kind` cluster.
- **Observability Agent** (`backend/agents/observability_agent.py`): reads a Rollout's live status from the Kubernetes API (falling back to a plain Deployment's status for anything deployed before this milestone), queries Prometheus for pod restart counts and waiting-reason counts (`CrashLoopBackOff`, `ImagePullBackOff`/`ErrImagePull`), and reads ArgoCD's currently-synced git revision. The actual rollback decision is 100% deterministic threshold logic against those facts -- the LLM (if Ollama is reachable) is only ever asked to phrase a plain-English summary of facts it's handed, the same narration-only pattern as the Docs Agent.
- **`POST /observe`**: on-demand check for one service (`{"service_name": "...", "namespace": "default"}`).
- **`POST /webhook/alertmanager`**: the automatic counterpart. A curated subset of kube-prometheus-stack's built-in alert rules (`KubePodCrashLooping`, `KubePodNotReady`, `KubeContainerWaiting`, etc.) are routed to this endpoint, which runs the same check whenever one fires.
- **`GET /recommendations`**: every recommendation ever made (on-demand or automatic), logged to `logs/observability_events.jsonl` -- this is where a human actually reviews and decides, since nothing here ever executes anything.
- **Rollback suggestion is a `git revert`, not `kubectl rollout undo`**: because ArgoCD's automated self-heal sync policy would just reapply the current (bad) Git state over any imperative cluster-level fix, the only rollback that actually sticks is reverting the offending commit and letting ArgoCD sync the reverted state back down.

### Verified with a real induced failure

Deployed a service with a genuinely nonexistent image tag (`nginx:this-tag-does-not-exist`) through the full real pipeline -- merged PR, Jenkins validated it, ArgoCD deployed it, and the pods stuck in `ImagePullBackOff`:

- `POST /observe` correctly flagged it: `recommended: true`, citing the Rollout's `Degraded` phase, 2 pods unable to pull their image, and 0/2 replicas available -- with a `git revert <real-commit-sha>` suggested command.
- The automatic path also worked: real `KubePodNotReady` alerts fired in Alertmanager on their own and hit `/webhook/alertmanager` four separate times over ~50 minutes, each one independently logging the same correct recommendation with zero manual intervention.
- Following the suggested `git revert` + push cleanly triggered ArgoCD's self-heal/prune to remove the broken service entirely, confirmed via `kubectl get applications -n argocd` and `kubectl get pods`.
- Caught and fixed a real bug in this pass: Kubernetes omits status fields entirely when their value is `0` (e.g. a fully-down Rollout has no `availableReplicas` key at all, rather than an explicit `0`), and the original code's `.get("availableReplicas")` read that as "unknown" rather than "zero," silently skipping the replica-availability check. Fixed by defaulting those fields to `0`.

Full setup and walkthrough: **`docs/milestone5-observability.md`**.

## Next milestone

Milestone 6 -- point ArgoCD at a real (or free-tier) AWS-hosted cluster for the final demo recording (see `docs/architecture.md` section 5 for keeping this near $0).
