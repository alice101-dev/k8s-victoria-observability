region = "asia-southeast2"
zone   = "asia-southeast2-a"
env    = "staging"

network_vpc_name = "vpc-observability"
subnet_key       = "gke"

cluster_name     = "observability"
regional_cluster = false
release_channel  = "STABLE"

master_ipv4_cidr_block   = "10.0.3.0/28"
cluster_ipv4_cidr_block  = "10.124.0.0/16"
services_ipv4_cidr_block = "10.125.0.0/22"

master_authorized_networks = [
  { cidr_block = "10.120.0.0/20", display_name = "vpc-internal" },
]

maintenance_start_time = "2026-01-04T18:00:00Z"
maintenance_recurrence = "FREQ=WEEKLY;BYDAY=SU,MO,TU,WE"

sa_account_id   = "observability-gke"
sa_display_name = "GKE node SA staging-observability"

node_pools = {
  "spot-pool" = {
    machine_type   = "e2-standard-2"
    min_node_count = 2
    max_node_count = 4
    spot           = true
    disk_size_gb   = 30
    disk_type      = "pd-balanced"
    labels         = { "workload" = "general" }
    tags           = ["staging-observability-node"]
  }
}
