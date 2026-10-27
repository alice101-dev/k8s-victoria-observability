data "google_project" "this" {
  project_id = var.project_id
}

locals {
  wi_subject = "principal://iam.googleapis.com/projects/${data.google_project.this.number}/locations/global/workloadIdentityPools/${var.project_id}.svc.id.goog/subject"
}

module "cloud_storage" {
  source = "../../../modules/cloud-storage"

  project_id = var.project_id
  region     = var.region
  env        = var.env
  buckets = {
    for k, b in var.buckets : "${var.project_id}-${var.env}-${k}" => merge(b, {
      iam = [for w in b.workload_identity : { role = w.role, member = "${local.wi_subject}/ns/${w.namespace}/sa/${w.service_account}" }]
    })
  }
}
