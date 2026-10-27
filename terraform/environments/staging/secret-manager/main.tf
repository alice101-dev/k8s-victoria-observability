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
