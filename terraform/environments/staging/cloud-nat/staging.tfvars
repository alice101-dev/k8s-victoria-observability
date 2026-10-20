# --- GCP provider ---
# project_id is not stored in the repo: set via TF_VAR_project_id from ../.env (Makefile).
region = "asia-southeast2"
zone   = "asia-southeast2-a"
env    = "staging"

# One Cloud NAT for vpc-observability: private GKE nodes need it to pull public images
# (ghcr.io, quay.io, docker.io), Grafana plugins and dashboards.
cloud_nat_configs = {
  "vpc-observability-main" = {
    vpc_name = "vpc-observability"
    region   = "asia-southeast2"

    # AUTO_ONLY: GCP-assigned egress IPs (no static IP cost). Use MANUAL_ONLY for a fixed
    # egress IP (third-party allowlists).
    nat_ip_allocate_option = "AUTO_ONLY"

    enable_dynamic_port_allocation = true
    min_ports_per_vm               = 64
    max_ports_per_vm               = 4096

    enable_logging = true
    log_filter     = "ERRORS_ONLY"
  }
}
