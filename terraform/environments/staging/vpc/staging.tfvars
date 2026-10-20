# --- GCP provider ---
# project_id is not stored in the repo: set via TF_VAR_project_id from ../.env (Makefile).
region = "asia-southeast2"
zone   = "asia-southeast2-a"
env    = "staging"

# --- VPC & Networking ---
# Result: VPC "staging-vpc-observability", subnet "staging-vpc-observability-gke-subnet".
network_vpc_name = "vpc-observability"
subnets = {
  gke = {
    # Node primary range. Pod and Service secondary ranges are created by GKE from the
    # CIDRs in the kubernetes-engine workspace.
    ip_cidr_range = "10.120.0.0/20"
    region        = "asia-southeast2"
  }
}
