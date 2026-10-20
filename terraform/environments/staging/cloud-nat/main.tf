# Cloud NAT for a single VPC (vpc-observability). Each cloud_nat_configs entry creates a Cloud
# Router, a NAT and optional static IPs. Add an entry for a secondary NAT on the same VPC;
# clone this workspace for another VPC to keep state isolated.

# The VPC must already exist (vpc workspace).
data "google_compute_network" "vpc" {
  for_each = var.cloud_nat_configs
  name     = "${var.env}-${each.value.vpc_name}"
}

# One Cloud NAT stack per cloud_nat_configs entry.
module "cloud_nat" {
  for_each = var.cloud_nat_configs
  source   = "../../../modules/cloud-nat"

  # Identity
  # nat_name = "<env>-<map key>", e.g. "staging-vpc-observability-main", unique per VPC.
  nat_name = "${var.env}-${each.key}"
  vpc_id   = data.google_compute_network.vpc[each.key].id
  region   = each.value.region
  env      = var.env

  # IP allocation
  nat_ip_allocate_option = each.value.nat_ip_allocate_option
  nat_ip_count           = each.value.nat_ip_count

  # Subnet scope
  source_subnetwork_ip_ranges_to_nat = each.value.source_subnetwork_ip_ranges_to_nat

  # Port allocation
  enable_dynamic_port_allocation = each.value.enable_dynamic_port_allocation
  min_ports_per_vm               = each.value.min_ports_per_vm
  max_ports_per_vm               = each.value.max_ports_per_vm

  # Connection timeouts
  tcp_established_idle_timeout_sec = each.value.tcp_established_idle_timeout_sec
  tcp_transitory_idle_timeout_sec  = each.value.tcp_transitory_idle_timeout_sec
  udp_idle_timeout_sec             = each.value.udp_idle_timeout_sec
  icmp_idle_timeout_sec            = each.value.icmp_idle_timeout_sec

  # Endpoint mapping
  enable_endpoint_independent_mapping = each.value.enable_endpoint_independent_mapping

  # Logging
  enable_logging = each.value.enable_logging
  log_filter     = each.value.log_filter
}
