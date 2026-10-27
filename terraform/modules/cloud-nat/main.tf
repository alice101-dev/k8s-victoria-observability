resource "google_compute_address" "nat_ips" {
  count  = var.nat_ip_allocate_option == "MANUAL_ONLY" ? var.nat_ip_count : 0
  name   = "${var.nat_name}-ip-${count.index + 1}"
  region = var.region

  description = "Static public IP ${count.index + 1} for Cloud NAT '${var.nat_name}' – managed by Terraform"

  labels = {
    env        = var.env
    managed_by = "terraform"
  }
}

resource "google_compute_router" "router" {
  name        = "${var.nat_name}-router"
  region      = var.region
  network     = var.vpc_id
  description = "Cloud Router for NAT '${var.nat_name}' – managed by Terraform"
}

resource "google_compute_router_nat" "nat" {
  name   = var.nat_name
  router = google_compute_router.router.name
  region = var.region

  nat_ip_allocate_option = var.nat_ip_allocate_option
  nat_ips                = var.nat_ip_allocate_option == "MANUAL_ONLY" ? google_compute_address.nat_ips[*].self_link : null

  source_subnetwork_ip_ranges_to_nat = var.source_subnetwork_ip_ranges_to_nat

  enable_dynamic_port_allocation = var.enable_dynamic_port_allocation
  min_ports_per_vm               = var.enable_dynamic_port_allocation ? var.min_ports_per_vm : null
  max_ports_per_vm               = var.enable_dynamic_port_allocation ? var.max_ports_per_vm : null

  tcp_established_idle_timeout_sec = var.tcp_established_idle_timeout_sec
  tcp_transitory_idle_timeout_sec  = var.tcp_transitory_idle_timeout_sec
  udp_idle_timeout_sec             = var.udp_idle_timeout_sec
  icmp_idle_timeout_sec            = var.icmp_idle_timeout_sec

  enable_endpoint_independent_mapping = var.enable_endpoint_independent_mapping

  log_config {
    enable = var.enable_logging
    filter = var.log_filter
  }
}
