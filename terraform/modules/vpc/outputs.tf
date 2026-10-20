output "vpc_id" {
  description = "VPC self-link ID"
  value       = google_compute_network.vpc.id
}

output "subnet_ids" {
  description = "Subnet IDs keyed like var.subnets"
  value       = { for k, v in google_compute_subnetwork.subnet : k => v.id }
}
