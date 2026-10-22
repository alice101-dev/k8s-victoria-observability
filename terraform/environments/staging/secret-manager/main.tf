# Secret Manager for the cluster workloads (needs the secretmanager API from the services workspace).
# Secret IDs are prefixed with the env: key "alertmanager-slack-webhook" → "staging-alertmanager-slack-webhook".
# Terraform creates the secrets and their IAM, never the values (see staging.tfvars).
# Access is granted to Kubernetes ServiceAccounts directly through Workload Identity Federation for
# GKE (principal:// identifiers): no Google service account, no key, no annotation in Git.
# The teardown keeps this workspace (a few cents a month), so the values survive environment rebuilds.
data "google_project" "this" {
  project_id = var.project_id
}

locals {
  wi_subject = "principal://iam.googleapis.com/projects/${data.google_project.this.number}/locations/global/workloadIdentityPools/${var.project_id}.svc.id.goog/subject"
}

module "secret_manager" {
  source = "../../../modules/secret-manager"

  project_id = var.project_id
  region     = var.region
  env        = var.env
  secrets = {
    for k, s in var.secrets : "${var.env}-${k}" => {
      labels = s.labels
      iam    = [for w in s.workload_identity : { role = w.role, member = "${local.wi_subject}/ns/${w.namespace}/sa/${w.service_account}" }]
    }
  }
}
