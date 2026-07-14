# Milestone 4: CI/CD + GitOps (Jenkins + ArgoCD + local Kubernetes)

This is the "merged code becomes a running service" half of the pipeline. Once you finish this guide, merging a PR will (a) trigger a Jenkins build that validates the generated Terraform/Helm, and (b) get picked up by ArgoCD, which deploys the Kubernetes/Helm side to a free local cluster running on your own machine.

**What stays free / what doesn't:** everything in this guide runs locally via Docker -- $0. The Terraform side (ECS, RDS, S3 on AWS) is validated but **never applied** by this pipeline; actually running `terraform apply` against AWS is a separate, deliberate action you'd take yourself later, since that's the one part of this project that costs real money (see `docs/architecture.md` section 5).

Total time: 30-45 minutes.

---

## Part 0 — What you're building

```
kind cluster (free, local, via Docker)
  └── ArgoCD  -- watches your GitHub repo, auto-deploys helm/<service>/ folders
  └── (your generated services land here as Kubernetes Deployments/Services)

Jenkins (free, local, via Docker)
  └── validates terraform/<service>/ and helm/<service>/ on every merge to main
  └── does NOT deploy anything -- ArgoCD does that independently
```

---

## Part 1 — Confirm Docker is installed

```
docker --version
```

If that fails, install Docker Desktop from **docker.com/products/docker-desktop** (free for personal use), open it once so it finishes its first-run setup, then re-run the command above.

**Using Colima instead of Docker Desktop?** That works fine -- kind only needs a working Docker API, which Colima provides. Just make sure it's actually running and that the `docker` CLI is pointed at it:
```
colima start --cpu 4 --memory 8   # if not already running
docker context ls                  # confirm "colima" is the active (*) context
docker context use colima           # if it isn't
docker ps                            # sanity check -- should return with no error
```
One gotcha specific to Colima (and to machines that previously had Docker Desktop installed and don't anymore): if `kind create cluster` fails with something like `error getting credentials -- err: exec: "docker-credential-desktop": executable file not found in $PATH`, your `~/.docker/config.json` still references Docker Desktop's credential helper, which doesn't exist in a Colima-only setup. Fix it once:
```
cat ~/.docker/config.json      # look for "credsStore": "desktop"
open -e ~/.docker/config.json  # remove that line, save, close
```
This only disables Docker's saved-login lookup (irrelevant for pulling kind's public node image) and is safe to do either way.

## Part 2 — Install kind, kubectl, and the ArgoCD CLI

```
brew install kind kubectl argocd
```

Confirm each:
```
kind --version
kubectl version --client
argocd version --client
```

## Part 3 — Create the local Kubernetes cluster

From the project root:
```
kind create cluster --config infra/kind/kind-config.yaml
```

This takes a minute or two the first time (downloads a node image). Confirm it's up:
```
kubectl cluster-info --context kind-ai-platform-engineer
kubectl get nodes
```
You should see one node in `Ready` state.

## Part 4 — Install ArgoCD into the cluster

```
kubectl create namespace argocd
kubectl apply -n argocd --server-side --force-conflicts -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
```

Wait for everything to come up (can take a couple of minutes):
```
kubectl wait --for=condition=Ready pods --all -n argocd --timeout=300s
```

## Part 5 — Log into ArgoCD

Open a new terminal tab (leave this running) and forward ArgoCD's UI to your machine:
```
kubectl port-forward svc/argocd-server -n argocd 8080:443
```

In another tab, get the auto-generated admin password:
```
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d; echo
```

Copy that password. Then log in via the CLI:
```
argocd login localhost:8080 --username admin --password 'PASTE_PASSWORD_HERE' --insecure
```

You can also open **https://localhost:8080** in your browser (click through the self-signed cert warning) and log in with the same `admin` / password to watch things visually.

## Part 6 — Give ArgoCD access to your repo

If you made your test repo **private** back in `docs/testing-github-pr.md`, ArgoCD needs credentials to read it (same for Jenkins in Part 8). Use the same fine-grained PAT from that guide (create a fresh one if it's expired):

```
argocd repo add https://github.com/YOUR-USERNAME/ai-platform-engineer-test.git \
  --username YOUR-USERNAME \
  --password 'YOUR_GITHUB_PAT'
```

If your repo is public, you can skip this step entirely.

## Part 7 — Apply the ApplicationSet

Open `infra/argocd/applicationset.yaml` in an editor and replace **both** occurrences of `REPO_URL_PLACEHOLDER` with your actual repo URL, e.g.:
```
https://github.com/YOUR-USERNAME/ai-platform-engineer-test.git
```

Save it, then apply:
```
kubectl apply -f infra/argocd/applicationset.yaml
```

Check it's registered:
```
argocd app list
```

If you already have a `helm/orders-api` folder on `main` from earlier testing, you should see an `orders-api` application appear here within a few seconds, likely `OutOfSync` until its first deploy.

## Part 8 — Start Jenkins

```
docker compose -f infra/jenkins/docker-compose.yml up -d --build
```

The first build takes a few minutes (it's installing Terraform/Helm/kubectl into the image). Once it's up:

1. Get the initial admin password:
   ```
   docker exec ai-platform-engineer-jenkins cat /var/jenkins_home/secrets/initialAdminPassword
   ```
2. Open **http://localhost:8081** in your browser, paste that password in.
3. Click **Install suggested plugins** and wait for it to finish.
4. Create your first admin user when prompted (any username/password you'll remember).
5. From the Jenkins dashboard: **New Item** -> name it `ai-platform-engineer` -> choose **Pipeline** -> OK.
6. Scroll to the **Pipeline** section -> Definition: **Pipeline script from SCM** -> SCM: **Git**.
7. Repository URL: your repo's URL (same as Part 6). If private, click **Add** next to Credentials, choose **Username with password**, and enter your GitHub username + the same fine-grained PAT as the password.
8. Branch Specifier: `*/main`.
9. Script Path: `Jenkinsfile` (already the default).
10. Save.
11. Click **Build Now** once to confirm it runs green. Open the build's **Console Output** to see the Terraform Validate / Helm Lint stages run for real.

From here on, Jenkins will also auto-check for new commits on `main` every 5 minutes on its own (see the `pollSCM` trigger in the `Jenkinsfile`) -- you don't have to click Build Now every time.

---

## Part 9 — The real test: merge a PR and watch it deploy

This is the part you asked to save for after this setup. One important note first:

**Use a real, pullable container image this time.** Every test so far used `myorg/orders-api:1.4.0`, which isn't a real image anywhere -- fine for validating generated code, but if ArgoCD actually deploys it to the kind cluster, the pod will sit in `ImagePullBackOff` forever since that image can't be pulled. To see genuinely **Running** pods, phrase your next request with a real public image, e.g.:

> "I need a new internal service called hello-world using image nginx:latest on port 80 with 2 replicas"

Then:

1. Send that request to `/generate` as usual (Ollama running, full pipeline runs, PR gets opened).
2. Open the PR on GitHub, look over the diff/description like a real reviewer would.
3. **Merge it.**
4. Within 5 minutes, Jenkins will pick it up on its next poll (or click **Build Now** in Jenkins to trigger it immediately) and validate the new `terraform/hello-world/` and `helm/hello-world/` folders.
5. Within ~3 minutes (ArgoCD's default polling interval), the ApplicationSet will notice the new `helm/hello-world/` folder and create a new `hello-world` Application automatically -- or force it immediately:
   ```
   argocd app list
   argocd app sync hello-world
   ```
6. Verify it's actually running in the cluster:
   ```
   kubectl get pods
   kubectl get deployments
   ```
   You should see `hello-world` pods in `Running` state (not `ImagePullBackOff`).
7. Confirm it's really serving traffic:
   ```
   kubectl port-forward svc/hello-world 8888:80
   curl http://localhost:8888
   ```
   You should get nginx's default welcome page HTML back.

That closes the loop end to end: a plain-English request became a reviewed PR, got merged, and is now a real running, reachable service on your machine -- with Jenkins and ArgoCD, not you, having done the deployment.

---

## Troubleshooting

- **`kind create cluster` fails or hangs**: make sure Docker Desktop is actually running (not just installed) and has at least 6-8GB of memory allocated (Docker Desktop -> Settings -> Resources).
- **ArgoCD pods stuck `Pending`**: usually means Docker doesn't have enough memory/CPU allocated. Bump the resources in Docker Desktop settings and try `kubectl delete namespace argocd` then redo Part 4.
- **`argocd login` fails with a cert error**: make sure you included `--insecure` (this is a local self-signed cert, expected).
- **ArgoCD app stuck `Unknown` or repo errors**: almost always missing repo credentials for a private repo -- redo Part 6, double-checking the PAT hasn't expired.
- **Jenkins can't clone the repo**: same as above but for Jenkins -- redo the credentials step in Part 8, step 7.
- **Pod stuck in `ImagePullBackOff`**: the image name in your request isn't a real, public image. Use something like `nginx:latest`, `hashicorp/http-echo`, or any other small public image.
- **Want to tear everything down and start clean**: `kind delete cluster --name ai-platform-engineer` and `docker compose -f infra/jenkins/docker-compose.yml down -v` remove the cluster and Jenkins respectively.
