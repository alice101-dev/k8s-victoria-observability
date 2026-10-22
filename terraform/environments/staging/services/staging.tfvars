# --- GCP provider ---
# project_id is not stored in the repo: set via TF_VAR_project_id from ../.env (Makefile).
region = "asia-southeast2" # Jakarta
zone   = "asia-southeast2-a"

# --- Enabled APIs (minimal set for this stack) ---
enabled_apis = [
  "artifactregistry.googleapis.com",     # Application images
  "cloudresourcemanager.googleapis.com", # Project management by Terraform
  "compute.googleapis.com",              # VPC, Cloud NAT, firewall, node VMs
  "container.googleapis.com",            # GKE
  "containerfilesystem.googleapis.com",  # GKE Image Streaming
  "containersecurity.googleapis.com",    # GKE security posture (BASIC)
  "iam.googleapis.com",                  # Node service account
  "iamcredentials.googleapis.com",       # Workload Identity
  "logging.googleapis.com",              # GKE system component logs
  "monitoring.googleapis.com",           # GKE system component metrics
  "serviceusage.googleapis.com",         # Used by Terraform to manage APIs
  "secretmanager.googleapis.com",        # Alertmanager Slack webhook (secret-manager workspace)
  "storage.googleapis.com",              # PostgreSQL backups (cloud-storage workspace)
]

# false: destroying this workspace never disables APIs.
disable_on_destroy = false
