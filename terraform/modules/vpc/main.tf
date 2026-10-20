# Custom-mode VPC with map-driven subnets. No deletion guard: test environment.
resource "google_compute_network" "vpc" {
  # checkov:skip=CKV2_GCP_18: firewall rules live in the separate 'firewall' workspace
  name                    = var.network_vpc_name
  description             = "${var.network_vpc_name} – managed by Terraform"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

# Name: <env>-<vpc>-<key>-subnet, e.g. staging-vpc-observability-gke-subnet
resource "google_compute_subnetwork" "subnet" {
  for_each = var.subnets
  # checkov:skip=CKV_GCP_26: VPC flow logs deferred (cost) in the test environment
  name                     = "${var.network_vpc_name}-${each.key}-subnet"
  ip_cidr_range            = each.value.ip_cidr_range
  region                   = each.value.region
  network                  = google_compute_network.vpc.id
  private_ip_google_access = each.value.private_ip_google_access
}
