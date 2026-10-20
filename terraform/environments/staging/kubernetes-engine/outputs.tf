output "cluster_name" {
  description = "GKE cluster name"
  value       = module.gke.cluster_name
}

output "location" {
  description = "Cluster location (zone or region)"
  value       = module.gke.location
}

output "get_credentials_command" {
  description = "kubeconfig command via the DNS endpoint (IAM-gated, no bastion)"
  value       = "gcloud container clusters get-credentials ${module.gke.cluster_name} --location ${module.gke.location} --project ${var.project_id} --dns-endpoint"
}

output "node_service_account" {
  description = "Node service account email"
  value       = module.gke.service_account_email
}
