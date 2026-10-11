#!/bin/bash
# Installs a single-node k3s cluster and makes it reachable from outside
# the instance (ArgoCD running on your local kind cluster needs to reach
# this API server over the internet). k3s's generated server certificate
# only covers IPs it's told about at install time, which is why the
# Elastic IP has to be allocated *before* this script runs, and passed in
# via --tls-san rather than discovered after the fact.
set -euo pipefail

curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server --tls-san ${public_ip} --write-kubeconfig-mode 644" sh -

# Rewrite the kubeconfig's server URL from the loopback address k3s writes
# by default to the Elastic IP, so it's directly usable from your laptop
# once copied off the box (see docs/milestone6-aws-demo-cluster.md).
sed -i "s#https://127.0.0.1:6443#https://${public_ip}:6443#" /etc/rancher/k3s/k3s.yaml
