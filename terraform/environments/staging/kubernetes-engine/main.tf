# --- VPC Data Sources ---
# VPC and subnet from the vpc workspace, looked up by name (no terraform_remote_state).
data "google_compute_network" "vpc" {
  name = "${var.env}-${var.network_vpc_name}"
}

data "google_compute_subnetwork" "subnet" {
  name   = "${var.env}-${var.network_vpc_name}-${var.subnet_key}-subnet"
  region = var.region
}

# --- GKE Cluster ---
module "gke" {
  source     = "../../../modules/kubernetes-engine"
  project_id = var.project_id
  env        = var.env

  # Zonal (default; control plane covered by the GKE free tier) or regional.
  location     = var.regional_cluster ? var.region : var.zone
  cluster_name = "${var.env}-${var.cluster_name}"
  vpc_id       = data.google_compute_network.vpc.id
  subnet_id    = data.google_compute_subnetwork.subnet.id

  master_ipv4_cidr_block     = var.master_ipv4_cidr_block
  cluster_ipv4_cidr_block    = var.cluster_ipv4_cidr_block
  services_ipv4_cidr_block   = var.services_ipv4_cidr_block
  master_authorized_networks = var.master_authorized_networks
  release_channel            = var.release_channel
  maintenance_start_time     = var.maintenance_start_time
  maintenance_recurrence     = var.maintenance_recurrence

  sa_account_id   = "${var.env}-${var.sa_account_id}"
  sa_display_name = var.sa_display_name

  node_pools = var.node_pools
}
