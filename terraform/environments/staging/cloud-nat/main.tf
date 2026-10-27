data "google_compute_network" "vpc" {
  for_each = var.cloud_nat_configs
  name     = "${var.env}-${each.value.vpc_name}"
}

module "cloud_nat" {
  for_each = var.cloud_nat_configs
  source   = "../../../modules/cloud-nat"

  nat_name = "${var.env}-${each.key}"
  vpc_id   = data.google_compute_network.vpc[each.key].id
  region   = each.value.region
  env      = var.env

  nat_ip_allocate_option = each.value.nat_ip_allocate_option
  nat_ip_count           = each.value.nat_ip_count

  source_subnetwork_ip_ranges_to_nat = each.value.source_subnetwork_ip_ranges_to_nat

  enable_dynamic_port_allocation = each.value.enable_dynamic_port_allocation
  min_ports_per_vm               = each.value.min_ports_per_vm
  max_ports_per_vm               = each.value.max_ports_per_vm

  tcp_established_idle_timeout_sec = each.value.tcp_established_idle_timeout_sec
  tcp_transitory_idle_timeout_sec  = each.value.tcp_transitory_idle_timeout_sec
  udp_idle_timeout_sec             = each.value.udp_idle_timeout_sec
  icmp_idle_timeout_sec            = each.value.icmp_idle_timeout_sec

  enable_endpoint_independent_mapping = each.value.enable_endpoint_independent_mapping

  enable_logging = each.value.enable_logging
  log_filter     = each.value.log_filter
}
