# --- GCP provider ---
# project_id is not stored in the repo: set via TF_VAR_project_id from ../.env (Makefile).
region = "asia-southeast2"
zone   = "asia-southeast2-a"
env    = "staging"

# --- VPC & Networking ---
network_vpc_name = "vpc-observability"
subnet_key       = "gke"

# --- GKE Cluster ---
# Result: cluster "staging-observability", zonal in asia-southeast2-a (single zone, Jakarta).
cluster_name     = "observability"
regional_cluster = false
release_channel  = "STABLE"

master_ipv4_cidr_block   = "10.0.3.0/28"
cluster_ipv4_cidr_block  = "10.124.0.0/16" # Pod
services_ipv4_cidr_block = "10.125.0.0/22" # Service

# Control plane IP endpoint only from inside the VPC; laptops use the IAM-gated DNS endpoint
# (gcloud container clusters get-credentials ... --dns-endpoint).
master_authorized_networks = [
  { cidr_block = "10.120.0.0/20", display_name = "vpc-internal" },
]

# Maintenance: 01:00–05:00 WIB Mon–Thu (start_time in UTC; 18:00Z = 01:00 WIB next day).
maintenance_start_time = "2026-01-04T18:00:00Z"
maintenance_recurrence = "FREQ=WEEKLY;BYDAY=SU,MO,TU,WE"

# --- Node service account ---
sa_account_id   = "observability-gke" # → staging-observability-gke
sa_display_name = "GKE node SA staging-observability"

# --- Node pool (Spot only) ---
# Total requests ≈ 1.6 vCPU / 5.3 GiB (+ ~0.5 vCPU / 0.7 GiB GKE system pods per node).
# 2 × e2-standard-2 fit; the autoscaler adds up to 4 nodes when pods are Pending
# (e.g. after a Spot preemption).
node_pools = {
  "spot-pool" = {
    machine_type   = "e2-standard-2" # 2 vCPU, 8 GB
    min_node_count = 2
    max_node_count = 4
    spot           = true
    disk_size_gb   = 30
    disk_type      = "pd-balanced"
    labels         = { "workload" = "general" }
    tags           = ["staging-observability-node"]
  }
}
