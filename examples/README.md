# Example: a fully verified real run

`orders-api-request.json` / `orders-api-response.json` is a real request and
response captured from a live run of the full pipeline -- not a mocked or
hand-written example. Every stage executed for real:

- **Planner**: real Ollama call (`qwen2.5-coder:7b`), not the heuristic
  fallback -- confirmed by `reports[0].notes` and by the model choosing
  `memory: 512Mi` on its own (the schema default is `256Mi`), which only
  happens when the LLM is actually reasoning about the request rather than
  a regex matching keywords.
- **Terraform**: `terraform validate` passed on the generated HCL (ECS
  Fargate service, security group, IAM role, S3 bucket, RDS instance).
- **Kubernetes**: `helm template` rendered successfully and `kubeconform`
  validated the resulting manifests against the Kubernetes API schema.
- **Security**: Checkov found 22 real findings in the Terraform (RDS
  encryption/monitoring/logging, S3 versioning/logging/encryption, security
  group hardening) and 17 in the Kubernetes manifest (missing probes, no
  securityContext, default namespace) -- genuine findings against a
  deliberately bare-bones generated service, not simulated output.
- **Policy**: Conftest passed our own Rego policy (`policies/opa/kubernetes.rego`)
  8/8 -- proof the generated Helm templates satisfy the org-owned policy by
  design (they always set resource requests/limits, never run privileged,
  never leave a Service selector empty).
- **Cost**: Infracost returned a real estimate, $42.47/month, broken down
  as ~$27.03 for the ECS Fargate service (vCPU + memory) and ~$15.44 for
  the RDS instance (compute + gp2 storage) -- computed statically from the
  Terraform HCL, no deployed environment or AWS credentials involved.
- **Docs + PR**: the PR summary paragraph is genuine LLM-written prose (not
  the templated fallback sentence), assembled together with the templated
  file list/validation/cost sections into a real GitHub pull request via
  `pr_client.py`.

The full Checkov/Infracost raw output in `orders-api-response.json` has
been trimmed to a summary for readability -- the pipeline's actual JSON
response includes the complete, unedited tool output in each `output`
field.
