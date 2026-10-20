output "cluster_id" {
  description = "GKE cluster ID"
  value       = google_container_cluster.primary.id
}

output "cluster_name" {
  description = "GKE cluster name"
  value       = google_container_cluster.primary.name
}

output "location" {
  description = "Cluster location (zone or region)"
  value       = google_container_cluster.primary.location
}

output "dns_endpoint" {
  description = "Control plane DNS endpoint (kubectl --dns-endpoint, helm provider)"
  value       = try(google_container_cluster.primary.control_plane_endpoints_config[0].dns_endpoint_config[0].endpoint, null)
  sensitive   = true
}

output "service_account_email" {
  description = "Node service account email"
  value       = google_service_account.gke_sa.email
}
