// CI pipeline for ai-platform-engineer.
//
// Scope, deliberately: this pipeline VALIDATES generated Terraform and
// Helm output as a merge-time gate (in addition to the same checks already
// run at PR-time by security_agent/terraform_agent/kubernetes_agent). It
// does NOT run `terraform apply` and does NOT deploy to Kubernetes itself.
//
// Why: `terraform apply` against the generated AWS resources would create
// real, billed infrastructure -- that has to be a deliberate, human-gated
// action, not something that happens automatically on every merge. And
// Kubernetes deployment is handled by ArgoCD via GitOps (it watches this
// repo directly), so Jenkins pushing to the cluster too would be a second,
// redundant, conflicting deployment path. Jenkins validates; ArgoCD ships.
pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
    }

    triggers {
        // Local demo has no public webhook endpoint for GitHub to call, so
        // Jenkins polls instead -- every 5 minutes. Swap this for a GitHub
        // webhook trigger later if this Jenkins instance ever gets a public
        // URL (e.g. via ngrok or a real deployment).
        pollSCM('H/5 * * * *')
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Terraform Validate') {
            steps {
                sh '''
                    set -e
                    if [ ! -d terraform ] || [ -z "$(ls -A terraform 2>/dev/null)" ]; then
                        echo "No generated terraform/ services yet -- nothing to validate."
                        exit 0
                    fi
                    for dir in terraform/*/; do
                        service=$(basename "$dir")
                        echo "== terraform validate: $service =="
                        (cd "$dir" && terraform init -backend=false -input=false && terraform validate)
                    done
                '''
            }
        }

        stage('Helm Lint') {
            steps {
                sh '''
                    set -e
                    if [ ! -d helm ] || [ -z "$(ls -A helm 2>/dev/null)" ]; then
                        echo "No generated helm/ services yet -- nothing to lint."
                        exit 0
                    fi
                    for dir in helm/*/; do
                        service=$(basename "$dir")
                        echo "== helm lint: $service =="
                        helm lint "$dir"
                    done
                '''
            }
        }

        stage('Summary') {
            steps {
                echo '''
                    Validation complete. This pipeline does not deploy anything:
                    - Kubernetes/Helm changes are picked up and deployed by ArgoCD
                      (see infra/argocd/applicationset.yaml), which watches this
                      repo directly and auto-syncs on its own schedule.
                    - Terraform changes are validated here but never applied --
                      running `terraform apply` against real AWS resources is a
                      deliberate step you take manually, on your own machine,
                      once you're ready to spend real money on AWS.
                '''
            }
        }
    }
}
