output "nat_names" {
  description = "Cloud NAT name per cloud_nat_configs entry"
  value       = { for k, m in module.cloud_nat : k => m.nat_name }
}

output "router_names" {
  description = "Cloud Router name per cloud_nat_configs entry"
  value       = { for k, m in module.cloud_nat : k => m.router_name }
}
