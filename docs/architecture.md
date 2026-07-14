# AI Platform Engineer — Architecture & Tech Stack

An AI-driven self-service platform that turns a natural-language request into a reviewed, deployed, and monitored change to your infrastructure — with a human always in the approval loop.

## 1. Pipeline overview

```
Developer NL request
    |
    v
Planner / Orchestrator (parses intent, builds a task plan)
    |
    +--> Terraform Agent      -> generates/updates .tf modules
    +--> Kubernetes Agent     -> generates Helm chart / manifests
    +--> Security Agent       -> Checkov + Trivy + OPA/Conftest scan
    +--> Cost Agent           -> Infracost estimate
    +--> Docs Agent           -> PR description, diagrams, rationale
    |
    v
GitHub Pull Request (code + scan results + cost delta + explanation)
    |
    v
Human review & approval (required -- nothing merges itself)
    |
    v
Jenkins (build, test, terraform plan, package Helm chart)
    |
    v
ArgoCD (GitOps sync to cluster)
    |
    v
Kubernetes cluster (AWS)
    |
    v
Prometheus -> Grafana -> Alertmanager
    |
    v
Observability Agent (watches rollout, summarizes outcome)
    |
    +--> on failure: Rollback Recommendation Agent
              (posts evidence + suggested `argocd rollback` / `kubectl rollout undo`
               command -- never executes it unless the human explicitly approves)
```

Everything above the "Kubernetes cluster (AWS)" line runs entirely on free/open-source tooling. AWS is the only line item where you may spend real money, and even that can be kept near $0 (see section 5).

## 2. Component-by-component stack

### Web UI
Streamlit or a small React app. Streamlit is the faster path to a working demo (single Python file, free, no separate frontend build) and is common enough in ML/platform tooling that it won't hurt the resume story. Swap to React later if you want a more polished screenshot.

### Backend / API
FastAPI. Free, async, has automatic OpenAPI docs (nice for a demo GIF), and is the de facto choice for Python agent backends.

### Orchestrator
A LangGraph (or plain Python state machine) orchestrator that owns a `TaskPlan` object and dispatches to each agent in sequence, collecting structured results (diffs, scan findings, cost delta) before assembling the PR. LangGraph is free/OSS and gives you a visualizable graph, which doubles as a good architecture diagram for interviews. A hand-rolled orchestrator (plain Python + Pydantic state) is equally valid and arguably easier to explain in an interview if you want full control instead of a framework.

### LLM (the "AI" in every agent)
Ollama running a local open-weight model -- fully free, no API cost, no data leaving your machine.
- Recommended default: **qwen2.5-coder:14b** (or the 7b variant if RAM-constrained) -- a code-specialized model that handles Terraform HCL and Kubernetes YAML well and runs on a normal laptop (8-16GB RAM).
- If you have a 24GB+ GPU or 32GB Mac: **qwen3-coder:30b** gives noticeably better generation quality.
- Be upfront in your resume/README that local models are weaker at IaC generation than frontier hosted models (Claude, GPT-4o) -- mitigate this with strong prompt templates, few-shot examples of "good" Terraform/Helm, and a validation loop (terraform validate / kubeconform) that forces the agent to self-correct before it ever reaches the PR. That retry-until-valid loop is actually a stronger engineering story than "the model got it right first try."
- Keep the LLM call behind an interface (`generate(prompt) -> str`) so swapping in Claude's API later (e.g., for a "pro mode") is a one-line config change, not a rewrite.

### Terraform Agent
- Generates `.tf` from templates + LLM fill-in for resource-specific parameters.
- Validates with `terraform validate` and `terraform plan` (both free, part of Terraform OSS).
- Keep a library of vetted module templates (VPC, ECS/EKS node group, RDS, S3, etc.) so the LLM is filling in parameters inside a known-good structure rather than free-writing HCL from scratch -- much more reliable.

### Kubernetes Agent
- Generates a Helm chart (or Kustomize overlay) per service.
- Validates with `helm template` + `kubeconform` (free, OSS successor to kubeval) to catch schema errors before they ever hit a cluster.

### Security Agent
Two free OSS layers, since they solve different problems:
- **Checkov** -- broadest open-source scanner (1000+ built-in rules across Terraform, Kubernetes, Helm), good default "did we do something obviously wrong" pass.
- **OPA/Conftest** -- write your own Rego policies for org-specific rules (e.g., "no public S3 buckets," "all Deployments must set resource limits"). This is what shows policy-as-code skills, not just running a linter.
- Note: **tfsec has been folded into Trivy** as of 2024 and no longer receives new checks -- use **Trivy** instead of tfsec if you also want container-image scanning in the same tool.

### Cost Agent
**Infracost CLI** -- free and open-source (Apache 2.0) for individual/CLI use, no account required for local `infracost diff` runs against a Terraform plan. The paid Infracost Cloud tier (dashboards, guardrails) is not needed for this project.

### Docs / Explanation Agent
LLM-generated PR description: what changed, why, security findings summary, cost delta, and a rollback plan -- assembled from the structured outputs of the other agents (don't let the LLM freeform the numbers; template them in).

### Git / PR automation
PyGithub (or the GitHub CLI `gh`) to open the PR, attach labels, and post scan/cost results as PR comments. GitHub itself is free for public repos (and free for private repos with generous limits).

### CI/CD -- Jenkins
Self-hosted, free, matches your original diagram. Run it as a Docker container on your laptop or on a free-tier VM (AWS EC2 t2.micro/t3.micro under the 12-month Free Tier, or a free-forever instance on Oracle Cloud). Pipeline stages: checkout -> terraform validate/plan -> helm lint -> unit tests -> package/push. Note the honest trade-off vs. GitHub Actions: Jenkins requires you to host and maintain it yourself (more ops work, more "real" DevOps story); GitHub Actions is zero-maintenance but a different resume narrative. You've chosen Jenkins -- worth a line in the README on why (matches enterprise on-prem CI patterns more closely than GitHub Actions does).

### GitOps -- ArgoCD
Free, OSS, install via Helm chart into your cluster. Watches a Git repo (or path) for the rendered manifests and syncs automatically. This is what actually deploys -- Jenkins never touches the cluster directly, which is the correct separation of concerns to describe in an interview.

### Kubernetes cluster
For local development and most demoing: **kind** or **k3d** (free, runs on your laptop, spins up in seconds). For the AWS component specifically, see section 5 for options ranging from $0 to a few dollars.

### Observability -- Prometheus, Grafana, Alertmanager
All free/OSS, installed via the `kube-prometheus-stack` Helm chart in one shot. Alertmanager routes rollout-failure alerts to your Observability Agent (via webhook) rather than (or in addition to) a human.

### Observability / Rollback Agent
After ArgoCD reports a sync, this agent polls Argo Rollouts status + Prometheus metrics (error rate, pod restarts, latency) for a fixed window, then:
- On success: LLM summarizes the rollout (metrics before/after, any warnings) and posts it back to the PR/Slack.
- On failure: gathers evidence (crash-loop logs, metric graphs, failed health checks), has the LLM produce a rollback recommendation with reasoning, and posts it -- but only executes `argocd app rollback` / `kubectl rollout undo` if a human explicitly confirms (a `/approve-rollback` chat command or a button in the UI). This human-in-the-loop gate is one of the more interview-worthy design decisions in the whole project -- be ready to explain why full autonomy here is a bad idea (blast radius, false positives from noisy metrics).

## 3. Suggested repo structure

```
ai-platform-engineer/
├── backend/
│   ├── main.py                  # FastAPI app
│   ├── orchestrator/
│   │   ├── planner.py
│   │   └── state.py
│   ├── agents/
│   │   ├── terraform_agent.py
│   │   ├── kubernetes_agent.py
│   │   ├── security_agent.py
│   │   ├── cost_agent.py
│   │   ├── docs_agent.py
│   │   └── observability_agent.py
│   ├── llm/
│   │   └── ollama_client.py     # swappable LLM interface
│   └── github/
│       └── pr_client.py
├── templates/
│   ├── terraform/                # vetted module templates
│   └── helm/                     # vetted chart templates
├── policies/
│   └── opa/                      # Rego policies
├── infra/
│   ├── jenkins/                  # Jenkinsfile, Docker setup
│   ├── argocd/                   # Application manifests
│   └── monitoring/               # kube-prometheus-stack values
├── ui/                            # Streamlit or React app
├── examples/                      # sample requests + generated output, for demo/README
└── README.md
```

## 4. Build roadmap (resume-friendly milestones)

1. **Milestone 1 -- Core codegen loop.** NL request -> Terraform + Helm output, validated locally (terraform validate, kubeconform). No PR yet. Proves the LLM+templates approach works.
2. **Milestone 2 -- Validation agents.** Wire in Checkov, OPA/Conftest, Infracost. Structured findings object.
3. **Milestone 3 -- GitHub integration.** Auto-opens a PR with generated code + assembled explanation + scan/cost results as comments.
4. **Milestone 4 -- CI/CD + GitOps.** Jenkins pipeline on merge, ArgoCD syncing to a local kind cluster.
5. **Milestone 5 -- Observability loop.** Prometheus/Grafana/Alertmanager wired up; Observability Agent polls and summarizes; failure path produces a rollback recommendation.
6. **Milestone 6 -- AWS demo cluster.** Point ArgoCD at a real (or free-tier) AWS cluster for the final demo recording.

Each milestone is independently demoable -- useful for showing incremental progress and for interview walkthroughs ("here's the state after milestone 3...").

## 5. Keeping AWS cost near zero

EKS's control plane alone costs ~$0.10/hour (~$73/month) if left running, which is the one place this project can quietly rack up a bill. Options, cheapest to most "authentic":

- **$0/month:** Run a single-node k3s (lightweight Kubernetes, not EKS) on a Free Tier EC2 t2.micro/t3.micro (750 hrs/month free for 12 months) or an Oracle Cloud Always Free VM. You get a real AWS-hosted cluster for the demo without any EKS control-plane charge. Slightly less "enterprise," but perfectly legitimate to describe as a cost-conscious engineering decision -- which is itself a good talking point.
- **A few dollars total:** Spin up a real EKS cluster only for recording your demo video/screenshots, then tear it down (`terraform destroy`) immediately after. A couple of hours of EKS + a small node group is on the order of $1-3.
- **Ongoing but still free-tier-eligible:** A single free-tier EC2 instance running k3s can stay up indefinitely at $0 during the 12-month window, so you can keep the live demo link working for recruiters.

Either path is honest to describe on a resume as "deployed to AWS" -- just be precise in the README about which one you used, since "EKS" and "k3s on EC2" are different claims to a technical interviewer.

## 6. Resume framing

A good one-line version: "Built an AI platform-engineering assistant that turns natural-language infra requests into validated Terraform/Kubernetes changes, complete with automated security/policy/cost checks, GitHub PR generation, Jenkins/ArgoCD GitOps deployment, and AI-driven rollout monitoring with human-gated rollback recommendations -- built entirely on free/open-source tooling plus a locally-hosted LLM."

Interviewers will likely probe two things hardest: (1) why the human-approval gates exist where they do (PR merge, rollback) -- have the blast-radius reasoning ready; (2) the reliability of LLM-generated infra code -- have the validate/retry loop and vetted-template approach ready as your answer, since "I constrain the model with known-good templates and a validation loop, not blind generation" is a much stronger answer than "the model just writes it."
