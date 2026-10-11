output "public_ip" {
  description = "Elastic IP of the k3s node."
  value       = aws_eip.k3s.public_ip
}

output "ssh_command" {
  description = "Run this to SSH into the node."
  value       = "ssh -i ${local_sensitive_file.private_key.filename} ubuntu@${aws_eip.k3s.public_ip}"
}

output "fetch_kubeconfig_command" {
  description = "Run this once cloud-init has finished (give it ~60s after apply) to pull the kubeconfig onto your laptop."
  value       = "scp -i ${local_sensitive_file.private_key.filename} ubuntu@${aws_eip.k3s.public_ip}:/etc/rancher/k3s/k3s.yaml ./k3s-demo-cluster-kubeconfig.yaml"
}

output "destroy_reminder" {
  description = "This is the only recurring cost in this project. Tear it down with `terraform destroy` when you're done recording the demo, unless you're deliberately keeping it up as a live recruiter-facing link."
  value       = "Run `terraform destroy` in infra/aws-demo-cluster/ when you no longer need this cluster running."
}
