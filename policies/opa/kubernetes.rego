# Org-specific policy-as-code, enforced via `conftest test` against the
# rendered Kubernetes manifest (helm template output). These are rules WE
# own and can change instantly, as opposed to Checkov's fixed built-in
# ruleset -- that's the point of running both.
#
# Written in Rego v1 syntax (OPA >= 1.0, finalized 2024): set-generating
# rules like `deny` require the `contains` keyword, and rule bodies require
# `if`. The older `deny[msg] { ... }` form is rejected by current
# conftest/OPA with a rego_parse_error -- this file uses the current form.
package main

deny contains msg if {
	input.kind == "Deployment"
	container := input.spec.template.spec.containers[_]
	not container.resources.requests
	msg := sprintf("Deployment '%s': container '%s' has no resource requests set", [input.metadata.name, container.name])
}

# Every container must declare resource limits, so one bad pod can't starve
# its node of CPU/memory.
deny contains msg if {
	input.kind == "Deployment"
	container := input.spec.template.spec.containers[_]
	not container.resources.limits
	msg := sprintf("Deployment '%s': container '%s' has no resource limits set", [input.metadata.name, container.name])
}

# Containers must never run in privileged mode.
deny contains msg if {
	input.kind == "Deployment"
	container := input.spec.template.spec.containers[_]
	container.securityContext.privileged == true
	msg := sprintf("Deployment '%s': container '%s' must not run as privileged", [input.metadata.name, container.name])
}

# LoadBalancer services (public-facing) must not use an empty selector,
# which would accidentally expose every pod in the namespace.
deny contains msg if {
	input.kind == "Service"
	input.spec.type == "LoadBalancer"
	count(input.spec.selector) == 0
	msg := sprintf("Service '%s': LoadBalancer service has no selector -- would expose all pods in namespace", [input.metadata.name])
}
