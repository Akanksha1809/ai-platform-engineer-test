variable "aws_region" {
  description = "AWS region for the demo cluster."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "Local AWS CLI profile to use (see ~/.aws/config)."
  type        = string
  default     = "ai-platform-engineer"
}

variable "instance_type" {
  description = "t3.micro (1GB RAM) turned out too small -- k3s's own baseline (etcd, apiserver, controller-manager, scheduler, kubelet, containerd, CoreDNS) alone left no headroom, causing constant restart-churn and API flakiness. t3.small (2GB) is no longer free-tier (~$7.50/month if left running) but is the cheapest size with actual headroom; destroy the cluster right after recording the demo to pay only cents of real usage."
  type        = string
  default     = "t3.small"
}

variable "allowed_cidr" {
  description = "CIDR allowed to reach SSH (22), the k3s API (6443), and NodePorts (30000-32767). Defaults to this machine's current public IP/32 -- NOT 0.0.0.0/0, since the k3s API would otherwise be open to the internet. If your ISP gives you a dynamic IP, re-run `terraform apply` after it changes, or override this variable."
  type        = string
}

variable "project_tag" {
  description = "Tag applied to every resource, so everything created for this milestone is easy to find and tear down."
  type        = string
  default     = "ai-platform-engineer-milestone6"
}
