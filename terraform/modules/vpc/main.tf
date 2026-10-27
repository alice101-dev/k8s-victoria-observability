resource "google_compute_network" "vpc" {
  name                    = var.network_vpc_name
  description             = "${var.network_vpc_name} – managed by Terraform"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

resource "google_compute_subnetwork" "subnet" {
  for_each                 = var.subnets
  name                     = "${var.network_vpc_name}-${each.key}-subnet"
  ip_cidr_range            = each.value.ip_cidr_range
  region                   = each.value.region
  network                  = google_compute_network.vpc.id
  private_ip_google_access = each.value.private_ip_google_access
}
