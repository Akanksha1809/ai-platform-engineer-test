# Org-specific policy-as-code, enforced via `conftest test` against the
# rendered Kubernetes manifest (helm template output). These are rules WE
# own and can change instantly, as opposed to Checkov's fixed built-in
# ruleset -- that's the point of running both.
#
# Written in Rego v1 syntax (OPA >= 1.0, finalized 2024): set-generating
# rules like `deny` require the `contains` keyword, and rule bodies require
# `if`. The older `deny[msg] { ... }` form is rejected by current
# conftest/OPA with a rego_parse_error -- this file uses the current form.
#
# As of Milestone 5, our Helm chart renders an Argo Rollouts "Rollout"
# instead of a plain apps/v1 Deployment (Rollout is a drop-in superset of
# Deployment's spec/template/selector fields). These rules check both
# kinds so they keep applying regardless of which workload type is used.
package main

workload_kinds := {"Deployment", "Rollout"}

deny contains msg if {
	input.kind in workload_kinds
	container := input.spec.template.spec.containers[_]
	not container.resources.requests
	msg := sprintf("%s '%s': container '%s' has no resource requests set", [input.kind, input.metadata.name, container.name])
}

# Every container must declare resource limits, so one bad pod can't starve
# its node of CPU/memory.
deny contains msg if {
	input.kind in workload_kinds
	container := input.spec.template.spec.containers[_]
	not container.resources.limits
	msg := sprintf("%s '%s': container '%s' has no resource limits set", [input.kind, input.metadata.name, container.name])
}

# Containers must never run in privileged mode.
deny contains msg if {
	input.kind in workload_kinds
	container := input.spec.template.spec.containers[_]
	container.securityContext.privileged == true
	msg := sprintf("%s '%s': container '%s' must not run as privileged", [input.kind, input.metadata.name, container.name])
}

# LoadBalancer services (public-facing) must not use an empty selector,
# which would accidentally expose every pod in the namespace.
deny contains msg if {
	input.kind == "Service"
	input.spec.type == "LoadBalancer"
	count(input.spec.selector) == 0
	msg := sprintf("Service '%s': LoadBalancer service has no selector -- would expose all pods in namespace", [input.metadata.name])
}
