# Milestone 6: a real AWS-hosted demo cluster

This milestone points the same ArgoCD instance (running in the local `kind`
cluster from Milestone 4) at a second, real cluster: a single-node k3s
install on an EC2 instance, registered as an additional ArgoCD destination.
`hello-nginx` is deployed to both -- the local cluster is still the
everyday dev/demo target; the AWS cluster exists to prove the same GitOps
flow reaches a real cloud cluster, and to back a live, resume-worthy URL.

This is k3s on EC2, not EKS. EKS's control plane alone costs ~$73/month
left running; this setup costs either $0 (free-tier `t3.micro`, see the
gotcha below for why we moved off it) or a few dollars a month at
`t3.small`. Be precise about this distinction with anyone technical --
"k3s on EC2" and "EKS" are different claims. See `docs/architecture.md`
section 5 for the full cost discussion.

## What's here

```
infra/aws/iam-policy-ec2-demo.json     -- scoped IAM policy for the CLI user that runs Terraform
infra/aws-demo-cluster/
├── main.tf                             -- EC2 instance, security group, Elastic IP, key pair
├── variables.tf
├── outputs.tf
├── user_data.sh.tpl                     -- installs k3s, bakes the EIP into the API server's TLS SAN
├── terraform.tfvars.example             -- copy to terraform.tfvars and set allowed_cidr to your own IP
└── (terraform.tfvars, *.pem, *kubeconfig* -- gitignored; machine- and secret-specific)
infra/argocd/hello-nginx-aws-demo-cluster.yaml  -- the ArgoCD Application targeting the AWS cluster
```

## Setup, from scratch

1. **A dedicated IAM user**, not root. Root access keys can't be scoped and
   are a standing liability for CLI/API use. Create a user with
   programmatic access only, attach `infra/aws/iam-policy-ec2-demo.json`
   as an inline or managed policy (EC2 only -- instances, security groups,
   Elastic IPs, key pairs, tags; nothing else), then create its access key
   and configure a local profile:
   ```bash
   aws configure --profile ai-platform-engineer
   ```
2. **Set your allowed CIDR.** `cp infra/aws-demo-cluster/terraform.tfvars.example infra/aws-demo-cluster/terraform.tfvars`
   and set `allowed_cidr` to `$(curl -s https://checkip.amazonaws.com)/32`.
   The security group only opens SSH, the k3s API (6443), and the NodePort
   range to this one IP -- not `0.0.0.0/0`. If your ISP gives you a
   dynamic IP, re-run `terraform apply` after it changes.
3. **Apply:**
   ```bash
   cd infra/aws-demo-cluster
   terraform init
   terraform plan -out=tfplan   # review it
   terraform apply "tfplan"
   ```
4. **Wait for cloud-init** (~60-90s), then fetch the kubeconfig:
   ```bash
   terraform output fetch_kubeconfig_command   # prints the exact scp command
   ```
5. **Register the cluster with ArgoCD** (run from the repo root, with a
   kubeconfig that has both the local `kind` context and the new
   `aws-demo-k3s` context -- `kubectl config view --flatten` after
   renaming the fetched kubeconfig's context):
   ```bash
   argocd login <your-argocd-server> --username admin
   argocd cluster add aws-demo-k3s --name aws-demo-cluster
   ```
6. **Deploy to it:**
   ```bash
   kubectl apply -f infra/argocd/hello-nginx-aws-demo-cluster.yaml
   ```

## Two real problems hit during setup (and the fixes)

**1. Jumbo-frame MTU blackhole over the public internet.** The Ubuntu AMI's
`ens5` interface defaults to `mtu 9001` (intended for intra-VPC jumbo
frames). Over the public internet -- where ICMP is often filtered, which
breaks Path MTU Discovery's feedback loop -- this causes TLS handshakes to
intermittently hang or time out: small packets (the initial TCP handshake)
get through fine, but the larger certificate-exchange packets silently
vanish. Fixed by forcing `mtu: 1500` via two netplan files on the instance
(`/etc/netplan/50-cloud-init.yaml` and a defensive `60-mtu-override.yaml`
drop-in, in case cloud-init ever regenerates the first one) -- confirmed
to survive a reboot.

**2. `t3.micro` (1GB RAM) has no real headroom for k3s.** k3s bundles
etcd/sqlite, the API server, controller-manager, scheduler, kubelet, and
containerd into one process, plus CoreDNS, local-path-provisioner,
Traefik, and metrics-server by default. On a `t3.micro` this pins
available memory in the tens of MB with load average sitting at 3-4 on a
2-vCPU burstable instance -- not an occasional blip, a permanent
restart-churn state, which is what made the ArgoCD connection look
"flaky" (it wasn't the network; the box was starving). Disabling the
unused addons (`disable: [traefik, servicelb, metrics-server]` in
`/etc/rancher/k3s/config.yaml`) didn't fully resolve it even after a k3s
restart -- the already-created Jobs kept restart-looping because there
still wasn't enough memory for the addon controller to finish tearing
them down. The real fix was resizing to `t3.small` (2GB): available memory
went from ~35MB to ~1.1GB, load average from 3-4 to 0.3, and the disabled
addons finally cleaned up completely on their own.

**Takeaway for the resume-framing talking point:** `t3.small` is the
realistic free-tier-adjacent floor for a k3s cluster you actually want to
manage remotely, not `t3.micro`. `t3.micro` is still fine for a workload
that doesn't need an orchestrator babysitting it.

## Verified

- `argocd cluster add` succeeded; `argocd cluster list` shows
  `aws-demo-cluster` with `STATUS: Successful`.
- `hello-nginx-aws` Application: `Synced`, both pods `2/2 Running` on the
  real cluster (`kubectl get pods -n default` against the fetched
  kubeconfig).
- The NodePort service (ServiceLB is disabled, so no cloud load balancer)
  is reachable from the public internet: `curl http://<public_ip>:<nodeport>/`
  returns the real nginx welcome page.

## Cost control

The EC2 instance (`t3.small`) is the only recurring cost in this entire
project -- everything else is free/local. Tear it down when you're not
actively using it:
```bash
cd infra/aws-demo-cluster
terraform destroy
```
This also releases the Elastic IP, so if you re-apply later you'll get a
new public IP -- update the ArgoCD cluster registration and the
`hello-nginx-aws-demo-cluster.yaml` destination accordingly (or just
re-run the setup steps above from step 4).
