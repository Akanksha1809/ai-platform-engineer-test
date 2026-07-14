# Self-hosted Jenkins, matching the original architecture diagram (as
# opposed to GitHub Actions, which would need no hosting at all -- see
# docs/architecture.md for why Jenkins was chosen here). This extends the
# official image with the CLI tools the Jenkinsfile needs, since the base
# image has none of them.
FROM jenkins/jenkins:lts-jdk17

USER root

# Terraform (HashiCorp's own apt repo, matching docs/verify-first-half.md's
# macOS install path via the hashicorp/tap Homebrew tap). Only gnupg + curl
# are actually needed here -- apt resolves the correct architecture
# (amd64/arm64) for the terraform package on its own.
RUN apt-get update && apt-get install -y --no-install-recommends \
      gnupg curl \
    && curl -fsSL https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(. /etc/os-release && echo $VERSION_CODENAME) main" \
       > /etc/apt/sources.list.d/hashicorp.list \
    && apt-get update && apt-get install -y terraform \
    && rm -rf /var/lib/apt/lists/*

# Helm (its install script auto-detects architecture on its own)
RUN curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

# kubectl (used only to let Jenkins inspect cluster state for its own
# validation stage -- ArgoCD, not Jenkins, is what actually deploys).
# Architecture-aware: this image may be amd64 or arm64 depending on the
# host (e.g. Apple Silicon Macs running Colima build arm64 images), and
# kubectl's download URL needs the matching binary or it won't execute.
RUN ARCH=$(dpkg --print-architecture) \
    && curl -fsSLO "https://dl.k8s.io/release/$(curl -Ls https://dl.k8s.io/release/stable.txt)/bin/linux/${ARCH}/kubectl" \
    && install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl \
    && rm kubectl

USER jenkins
