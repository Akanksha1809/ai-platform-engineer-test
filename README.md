# AI Platform Engineer

An AI agent that turns a plain-English infrastructure request into a validated, cost-estimated, security-scanned GitHub pull request — with an AI-written explanation that's constrained to never invent a fact. Once a human merges that PR, CI validates it and GitOps deploys it, as an Argo Rollouts canary, to a Kubernetes cluster — a local one for development, and a real AWS-hosted one for a genuine, internet-reachable demo target. Once deployed, an Observability Agent watches the rollout and *recommends* — but never executes — a rollback if something looks wrong.

It runs on a locally-hosted LLM (Ollama, `qwen2.5-coder:3b`) and costs $0 except for one small, deliberately contained AWS line item (see [Cost](#cost) below). Every external tool call — terraform, helm, checkov, conftest, infracost, the GitHub API, Prometheus — degrades gracefully if that tool isn't installed or reachable, so the whole pipeline is demoable on a machine with nothing but Python on it, and gets progressively more real as you install things.

## How it works

```
NL request
  -> planner            (Ollama, or a heuristic fallback)        -> ServiceSpec
  -> terraform_agent      renders Terraform from templates + `terraform validate`
  -> kubernetes_agent      renders a Helm chart + `helm template` / `kubeconform`
  -> security_agent        checkov (terraform + kubernetes) + conftest (our own Rego policies)
  -> cost_agent             infracost breakdown -> a real monthly cost estimate
  -> docs_agent              assembles the PR title/body (templated facts + one constrained LLM sentence)
  -> pr_client                 opens the GitHub PR via the REST API (PyGithub)
```

Every stage reads and writes the same `TaskPlan` object (`backend/orchestrator/state.py`) — one `ServiceSpec`, a list of per-agent `AgentReport`s, and the final `pr_title`/`pr_body`. The rule enforced everywhere: **numbers and pass/fail results in the PR are always templated in directly from these structured reports. The LLM is only ever asked for a short, constrained "why" paragraph** — it never restates a cost figure or scan result from memory, so it can't misreport one. This is also why the generated infrastructure itself is trustworthy: the model never writes Terraform or Kubernetes YAML directly — it fills a constrained `ServiceSpec`, which is rendered from hand-written, vetted Jinja2 templates and then independently validated before a human ever sees it.

Once a generated PR is merged to `main`, two systems act, deliberately decoupled from each other:

- **Jenkins** (self-hosted locally) runs `terraform validate` and `helm lint` on every generated folder. It is a **validate-only gate** — it never runs `terraform apply` and never deploys anything.
- **ArgoCD**, running in a local `kind` cluster, is what actually ships the Kubernetes side. Its `ApplicationSet` watches the repo with a Git directory generator matching `helm/*` — every new `helm/<service>/` folder that lands on `main` automatically becomes its own ArgoCD `Application`, synced with pruning and self-heal. No one hand-writes an Application manifest per service.

The Helm chart's workload template renders an **Argo Rollouts canary** (`argoproj.io/v1alpha1 Rollout`) instead of a plain Deployment — a basic, replica-weighted canary (50% traffic, a 30s pause, then auto-promote), with no service-mesh dependency.

Once deployed, the **Observability Agent** combines three independent, factual reads with pure threshold logic — no LLM in the decision path:
1. The Rollout's (or a plain Deployment's, for anything older) live status from the Kubernetes API.
2. Prometheus, queried for pod restart counts and waiting-reason counts (`CrashLoopBackOff`, `ImagePullBackOff`/`ErrImagePull`).
3. ArgoCD's currently-synced Git revision.

The LLM, if reachable, is only ever asked to phrase a plain-English summary of facts it's handed — the same narration-only pattern as the PR body. Two paths trigger it: `POST /observe` (on-demand, for one named service) and `POST /webhook/alertmanager` (automatic — a curated set of Alertmanager rules route straight to this endpoint). Every recommendation, from either path, is logged for a human to read and act on. **Nothing here ever executes a rollback.** The suggested command is always `git revert`, never `kubectl rollout undo` — ArgoCD's self-heal sync policy would just reapply the current (broken) Git state over any imperative cluster-level fix, so reverting the offending commit and letting ArgoCD sync the reverted state back down is the only rollback that actually sticks.

The same ArgoCD instance also manages a **second, real cluster**: a single-node [k3s](https://k3s.io/) install on an EC2 instance, provisioned with Terraform behind a security group locked to one IP. This is deliberately k3s on EC2, not EKS — EKS's control plane alone costs ~$73/month left running, while this setup is either $0 (free-tier eligible, torn down between uses) or a few dollars a month. A generated service can be deployed there the same way as locally, over the exact same GitOps flow, proving the pipeline reaches a genuinely internet-reachable cluster, not just a laptop.

## The two human-approval gates, and nowhere else

Exactly two points in this system require a person: **merging a generated PR**, and **acting on a rollback recommendation**. Both sit at the same place on the blast-radius curve — the step where being wrong is expensive and hard to undo. Everything upstream of them (codegen, scanning, cost estimation, health evaluation) is cheap to be wrong about, so none of it is gated. Nothing in this project applies real infrastructure or executes a rollback without a human reading something first.

## Setup

```bash
# 1. Python deps
pip install -r requirements.txt

# 2. (Optional but recommended) a local LLM for real NL understanding + PR explanations
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &
ollama pull qwen2.5-coder:3b   # a 7b/14b variant works too, just slower on a lower-RAM machine

# 3. (Optional) validation tools -- generation/scanning work without them,
#    results just show "skipped" with a clear reason until installed.
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

New to this? A full click-by-click walkthrough (creating the test repo, the token, everything) is in `docs/testing-github-pr.md`, and `scripts/test_pr_client.py` lets you test just the PR step without running the whole server. `scripts/check_tools.sh` shows at a glance what's installed; `docs/verify-first-half.md` walks through installing everything so every stage runs for real instead of gracefully skipping.

## Run

```bash
source venv/bin/activate   # if you're using one
uvicorn backend.main:app --reload --port 8000
```

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"request": "I need a new public service called orders-api using image myorg/orders-api:1.4.0 on port 8080 with 3 replicas, needs an s3 bucket and a postgres database"}'
```

The response's `pr_title`/`pr_body` fields are exactly what gets posted to GitHub, so you can inspect them even without `GITHUB_TOKEN` set. `pr_result` tells you whether a PR was actually opened (`attempted`, `created`, `url`, `branch`, or a `message` explaining why not — the pipeline always completes regardless).

Once something's deployed:

```bash
curl -X POST http://localhost:8000/observe \
  -H "Content-Type: application/json" \
  -d '{"service_name": "orders-api", "namespace": "default"}'
```

`GET /recommendations` lists everything the Observability Agent has ever recommended, on-demand or automatic.

## Proven in practice, not just in theory

- **Real GitHub PR automation.** Multiple real, independently-generated services have been merged as real pull requests against a real repository — not simulated. Each PR's title, body, validation results, and cost estimate were produced entirely by the pipeline from a plain-English request.
- **A real induced failure, caught both ways.** A service was deployed with a genuinely nonexistent image tag through the full real pipeline — merged PR, Jenkins-validated, ArgoCD-deployed — and its pods stuck in `ImagePullBackOff`. `POST /observe` correctly flagged it: `recommended: true`, citing the Rollout's `Degraded` phase and the exact replica shortfall, with a real `git revert <sha>` command pointing at the actual commit that broke it. Separately, the automatic path also worked unprompted: real `KubePodNotReady` alerts fired in Alertmanager on their own and hit the webhook repeatedly over the following hour, each one independently logging the same correct recommendation with zero manual intervention. Following the suggested revert cleanly triggered ArgoCD's self-heal to remove the broken service.
- **A real, internet-reachable AWS cluster.** The k3s-on-EC2 cluster isn't a mockup — a generated service, deployed to it by ArgoCD the same way as the local cluster, is reachable by `curl` from the public internet, serving real traffic from a real pod on a real EC2 instance.
- **Real bugs found and fixed during the build**, each diagnosed from a specific signature in the symptoms, not guessed at: Python's `str`-subclassed `Enum.__str__` prints `ClassName.MEMBER` instead of the plain value, breaking the generated PR body's resource summary; Kubernetes omits zero-valued status fields entirely rather than sending an explicit `0`, which silently broke the replica-availability check; and a jumbo-frame default MTU on the EC2 instance's network interface black-holed TLS handshakes over the public internet, diagnosed from the specific pattern of fast TCP connects paired with slow or failing TLS.

## Cost

Genuinely $0: Ollama, the local `kind` cluster, Jenkins, ArgoCD, Prometheus/Grafana, every validation tool, GitHub's API. The one real, ongoing line item is the AWS EC2 instance running k3s (`t3.small` — `t3.micro` turned out to have no real memory headroom for k3s plus ArgoCD's management overhead) — roughly **$7.50/month if left running continuously**, or effectively nothing if torn down between uses (`terraform destroy` in `infra/aws-demo-cluster/`). Full detail, including the free-tier math and why EKS was ruled out: `docs/architecture.md` section 5.

## Repo layout

```
backend/
├── main.py                          # FastAPI app: /generate, /observe, /webhook/alertmanager, /recommendations
├── orchestrator/
│   ├── state.py                     # ServiceSpec / TaskPlan / ValidationResult / CostEstimate / PRResult / ObservabilityReport
│   ├── planner.py                   # NL -> ServiceSpec (Ollama, or heuristic fallback)
│   └── pipeline.py                  # wires the six generation stages together
├── agents/
│   ├── terraform_agent.py           # render + terraform validate
│   ├── kubernetes_agent.py          # render + helm template/kubeconform; also exposes render_manifest() for security_agent
│   ├── security_agent.py            # checkov + conftest
│   ├── cost_agent.py                # infracost
│   ├── docs_agent.py                # PR title/body assembly
│   └── observability_agent.py       # Rollout/Deployment status + Prometheus + rollback recommendation
├── github/pr_client.py              # PyGithub REST API calls
└── llm/ollama_client.py             # the ONLY place that talks to Ollama -- swapping LLM backends later is a one-file change

templates/{terraform,helm}/service/  # Jinja2 templates the agents render from
policies/opa/kubernetes.rego          # this project's own conftest policies

infra/
├── kind/kind-config.yaml             # the local Kubernetes cluster definition
├── argocd/applicationset.yaml        # auto-discovers helm/<service>/ folders
├── jenkins/                          # Dockerfile + compose for self-hosted Jenkins
├── monitoring/alertmanager-values.yaml  # routes rollout-failure alerts to our webhook
├── aws/iam-policy-ec2-demo.json      # the scoped IAM policy for the AWS cluster's Terraform
└── aws-demo-cluster/                 # Terraform for the real EC2+k3s cluster
    ├── main.tf / variables.tf / outputs.tf
    └── user_data.sh.tpl               # installs k3s, bakes the Elastic IP into its TLS SAN

Jenkinsfile                           # validates on merge; never deploys
docs/                                  # architecture.md (full design doc + cost math), plus a deep-dive per subsystem
```

## Further reading

- `docs/architecture.md` — the full design rationale, including the cost math and why each tool was chosen.
- `docs/milestone4-cicd-gitops.md` — the Jenkins + ArgoCD setup and a merge-to-running-pod walkthrough.
- `docs/milestone5-observability.md` — the Observability Agent, canary rollout setup, and the full induced-failure test writeup.
- `docs/milestone6-aws-demo-cluster.md` — the AWS cluster setup, including the MTU and memory-sizing issues hit and fixed along the way.
- `docs/testing-github-pr.md` / `docs/verify-first-half.md` — click-by-click setup guides for making every stage run for real.
