# Milestone 5: Observability + rollback recommendations (Argo Rollouts + Prometheus)

This is the "AI watches what it just deployed" half of the pipeline. Once you finish this guide, every
service ArgoCD deploys is a canary-strategy Argo Rollout, and an Observability Agent can check its
health -- on demand, or automatically when Prometheus's Alertmanager fires a real alert -- and
recommend, but never execute, a rollback with a concrete `git revert` command as evidence.

**What stays free:** everything here is local, free/OSS tooling (Argo Rollouts, kube-prometheus-stack,
Ollama). No AWS resources are touched by this milestone at all.

Total time: 45-60 minutes, plus however long you let a real Alertmanager alert take to fire (10-20+
minutes) if you want to see the fully automatic path, not just the on-demand one.

---

## Part 0 — What you're building

```
kind cluster (already running from Milestone 4)
  └── Argo Rollouts controller  -- new: drives canary rollouts for every service
  └── kube-prometheus-stack     -- new: Prometheus + Alertmanager + Grafana + kube-state-metrics
  └── ArgoCD                    -- unchanged: still watches the repo and deploys helm/<service>/

Your backend (FastAPI, on your Mac)
  └── POST /observe               -- on-demand: "is <service> healthy right now?"
  └── POST /webhook/alertmanager  -- automatic: Alertmanager calls this when something's wrong
  └── GET /recommendations        -- human-facing log of every recommendation ever made
```

The actual go/no-go decision is deterministic (replica counts, restart counts, crash/image-pull
signals) -- the LLM is only ever asked to phrase a plain-English summary of facts it's handed, never to
decide anything. Nothing in this milestone ever runs a rollback; a human always reads the
recommendation and decides.

---

## Part 1 — Install Argo Rollouts

```
kubectl create namespace argo-rollouts
kubectl apply -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml
kubectl get pods -n argo-rollouts
```

Wait for the single controller pod to show `1/1 Running` (one pod is correct -- this isn't an HA setup).

Install the CLI plugin too, for human-readable rollout status later:
```
brew install argoproj/tap/kubectl-argo-rollouts
kubectl argo rollouts version
```

## Part 2 — Chart change: Deployment → Rollout

`templates/helm/service/templates/rollout.yaml` (renamed from `deployment.yaml`) now renders an
`argoproj.io/v1alpha1 Rollout` instead of a plain `apps/v1 Deployment`. Rollout is a drop-in superset of
Deployment's spec fields, plus a `strategy.canary` block:

```yaml
strategy:
  canary:
    steps:
      - setWeight: 50
      - pause:
          duration: 30s
```

This is "basic canary" -- replica-weighted, no service mesh or traffic-router plugin required. On a
service's very first deploy there's no prior stable version to canary against, so Argo Rollouts goes
straight to 100% (you'll only see the 50%/pause split on a service's *second* revision onward).

`kubeconform` now validates the rendered `Rollout` against the real Argo Rollouts CRD JSON schema (from
the community-maintained `datreeio/CRDs-catalog` project), not just built-in Kubernetes kinds --
`backend/agents/kubernetes_agent.py`'s kubeconform invocation adds `-schema-location` pointing at it,
with `-ignore-missing-schemas` as a fallback. `policies/opa/kubernetes.rego`'s resource-request/limit/
privileged rules now match on `input.kind in {"Deployment", "Rollout"}` so they keep applying to both.

No action needed here if you're following this after the fact -- these are already in the repo. If
you're regenerating a service, note that `env: []` must be rendered explicitly for containers with no
environment variables (rather than a bare `env:` key, which YAML parses as `null`) -- the stricter
Rollout CRD schema requires `env` to be an array, unlike Deployment's more lenient built-in schema.

## Part 3 — Install kube-prometheus-stack

```
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
kubectl create namespace monitoring
helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack -n monitoring
kubectl get pods -n monitoring
```

Wait a few minutes for all 6 pods (`alertmanager-*`, `*-grafana-*`, `*-kube-state-metrics-*`,
`*-operator-*`, `*-node-exporter-*`, `prometheus-*`) to show `Running`.

Sanity-check it's actually scraping:
```
kubectl port-forward -n monitoring svc/kube-prometheus-stack-prometheus 9090:9090
```
then, in another tab:
```
curl -s 'http://localhost:9090/api/v1/query?query=up' | python3 -m json.tool
```
You should see several targets with `"value": [..., "1"]`. (A few control-plane targets like
`kube-scheduler`/`kube-proxy`/`etcd` commonly show `0` on `kind` -- that's a known `kind` quirk, unrelated
to anything we need here.)

## Part 4 — Backend: the Observability Agent

`backend/agents/observability_agent.py` does three things:

1. **Rollout status**: `kubectl get rollout <service> -n <namespace> -o json`, falling back to
   `kubectl get deployment` for anything deployed before this milestone. Kubernetes omits status fields
   entirely when their value is `0` (rather than an explicit `0`), so these are read with `.get(key, 0)`
   defaults -- getting this wrong (reading a missing key as "unknown" instead of "zero") was a real bug
   caught during testing (see Part 6).
2. **Prometheus metrics**: restart count, `CrashLoopBackOff` pod count, `ImagePullBackOff`/`ErrImagePull`
   pod count, not-ready pod count -- all from `kube-state-metrics`, which works the same regardless of
   whether the workload is a Rollout or a Deployment, since both create ordinary Pods underneath.
3. **ArgoCD's synced revision**: `kubectl get application <name> -n argocd -o json`, read
   `.status.sync.revision` -- this is what turns a rollback recommendation into a concrete
   `git revert <sha>` instead of a vague "go back a version."

Make sure `PROMETHEUS_URL` (default `http://localhost:9090`) is reachable -- keep the port-forward from
Part 3 running whenever you use `/observe` or expect the webhook to work with real metrics.

Try it against anything already deployed:
```
curl -s -X POST http://localhost:8000/observe \
  -H "Content-Type: application/json" \
  -d '{"service_name": "hello-nginx", "namespace": "default"}' | python3 -m json.tool
```

## Part 5 — Automatic path: Alertmanager webhook

`backend/main.py` exposes `POST /webhook/alertmanager`, which Alertmanager calls with its standard
payload whenever a routed alert fires. It extracts `namespace`/`pod` from the alert's labels, derives the
service name from the pod name (`<service>-<replicaset-hash>-<pod-hash>` → `<service>`), runs the same
check as `/observe`, and appends the result to `logs/observability_events.jsonl` -- viewable via
`GET /recommendations`. It never executes anything.

Wire Alertmanager to it. First confirm a pod inside the cluster can actually reach your Mac (this goes
through kind's node container → Colima's VM → your Mac, which isn't guaranteed on every setup):
```
kubectl run tmp-curl --rm -it --restart=Never --image=curlimages/curl -- curl -s -m 5 http://host.docker.internal:8000/health
```
If that doesn't return `{"status":"ok"}`, your backend needs to be listening on all interfaces, not just
loopback -- start it with `uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000` (already the
default in `scripts/start_backend.sh`).

Apply the routing config:
```
helm upgrade kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  -n monitoring -f infra/monitoring/alertmanager-values.yaml --reuse-values
```

This routes a curated subset of kube-prometheus-stack's built-in alert rules (`KubePodCrashLooping`,
`KubePodNotReady`, `KubeContainerWaiting`, etc.) to our webhook; everything else (the many general
cluster-health alerts the chart ships by default) goes to a `null` receiver, since they aren't about our
services. Confirm it took:
```
kubectl port-forward -n monitoring svc/kube-prometheus-stack-alertmanager 9093:9093
curl -s http://localhost:9093/api/v2/status | python3 -c "import json,sys; print(json.load(sys.stdin)['config']['original'])"
```

## Part 6 — Prove it with a real, intentional failure

Deploy something that will genuinely fail, through the real pipeline (not a simulation):

```
curl -s -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"request": "I need a service called broken-app using image nginx:this-tag-does-not-exist on port 80 with 2 replicas"}'
```

Merge the resulting PR, trigger a Jenkins build (validation passes -- a bad tag doesn't fail
`terraform validate`/`helm lint`), and wait for ArgoCD to sync. Confirm the pods are stuck:
```
kubectl get pods -n default -l app=broken-app
```
You should see `ImagePullBackOff`.

Check the agent catches it:
```
curl -s -X POST http://localhost:8000/observe \
  -H "Content-Type: application/json" \
  -d '{"service_name": "broken-app", "namespace": "default"}' | python3 -m json.tool
```
Expect `recommended: true`, reasons citing the image-pull failures and (once ~10 minutes pass and Argo
Rollouts' `progressDeadlineSeconds` elapses) the `Degraded` phase, and a `suggested_command` with a real
`git revert <sha>` pointing at the merge commit.

If you're willing to wait 10-20+ minutes, you can also confirm the fully automatic path with no manual
`/observe` call at all:
```
curl -s http://localhost:8000/recommendations | python3 -m json.tool
```
Real `KubePodNotReady` alerts firing on their own will show up here, each with `triggered_by_alert` set
and the same correct recommendation.

**Clean up by actually following the recommendation** -- this is also the real test that the suggested
command works:
```
git revert -m 1 <the-merge-commit-sha> --no-edit
git push origin main
```
ArgoCD's `prune: true` self-heal policy will remove everything from that commit. Confirm:
```
kubectl get applications -n argocd
kubectl get pods -n default -l app=broken-app   # should show "No resources found"
```

---

## Why `git revert`, never `kubectl rollout undo`

ArgoCD's ApplicationSet is configured with `automated: {prune: true, selfHeal: true}` -- it continuously
reconciles the cluster to match what's in Git. An imperative fix like `kubectl rollout undo` or manually
editing a Deployment/Rollout would just get reverted right back by the next self-heal pass, since as far
as ArgoCD is concerned the current Git state is still "correct." The only rollback that actually sticks
in a GitOps setup is changing what's in Git and letting the existing sync policy do its job -- which is
exactly what the Observability Agent recommends, and exactly why it never tries to touch the cluster
directly.
