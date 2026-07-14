#!/usr/bin/env bash
# Prints which external tools the pipeline can see on PATH, and their
# version, so you can tell at a glance what's missing before running a
# real end-to-end test. Doesn't install anything -- just checks.
#
# Run from anywhere:
#   bash scripts/check_tools.sh

check() {
  local name="$1"
  local version_cmd="$2"
  if command -v "$name" >/dev/null 2>&1; then
    local version
    version=$(eval "$version_cmd" 2>/dev/null | head -n 1)
    printf "  \033[32m✅ %-12s\033[0m %s\n" "$name" "$version"
  else
    printf "  \033[31m❌ %-12s\033[0m not found on PATH\n" "$name"
  fi
}

echo "Checking tools used by ai-platform-engineer..."
echo
echo "LLM:"
check "ollama" "ollama --version"
echo
echo "Terraform / Kubernetes generation + validation:"
check "terraform" "terraform --version"
check "helm" "helm version --short"
check "kubeconform" "kubeconform -v"
echo
echo "Security / policy:"
check "checkov" "checkov --version"
check "conftest" "conftest --version"
echo
echo "Cost estimation:"
check "infracost" "infracost --version"
echo
echo "If ollama shows installed, also confirm the server is actually running:"
echo "  curl -s http://localhost:11434/api/tags"
echo "(should return JSON, not a connection-refused error)"
