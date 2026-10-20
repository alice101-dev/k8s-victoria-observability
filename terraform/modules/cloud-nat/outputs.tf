output "nat_name" {
  description = "Name of the Cloud NAT resource."
  value       = google_compute_router_nat.nat.name
}

output "router_name" {
  description = "Name of the Cloud Router backing this NAT."
  value       = google_compute_router.router.name
}

output "nat_id" {
  description = "Fully-qualified ID of the Cloud NAT resource."
  value       = google_compute_router_nat.nat.id
}

output "nat_ips" {
  description = "List of reserved static public IP addresses used by this Cloud NAT. Empty when using AUTO_ONLY allocation."
  value       = google_compute_address.nat_ips[*].address
}

output "nat_ip_self_links" {
  description = "Self-links of the reserved static public IPs. Useful for cross-module references."
  value       = google_compute_address.nat_ips[*].self_link
}
