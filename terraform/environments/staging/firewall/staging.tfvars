# --- GCP provider ---
# project_id is not stored in the repo: set via TF_VAR_project_id from ../.env (Makefile).
region = "asia-southeast2"
zone   = "asia-southeast2-a"
env    = "staging"

# --- VPC & Networking ---
network_vpc_name = "vpc-observability"

# GKE creates the rules it needs itself; this only adds SSH to nodes through IAP for debugging.
firewall_rules = {
  "allow-iap-ssh" = {
    description   = "Allow SSH to GKE nodes only from IAP TCP forwarding"
    direction     = "INGRESS"
    priority      = 1000
    source_ranges = ["35.235.240.0/20"]
    target_tags   = ["staging-observability-node"]
    log_config = {
      metadata = "INCLUDE_ALL_METADATA"
    }
    allow = [{
      protocol = "tcp"
      ports    = ["22"]
    }]
  }
}
