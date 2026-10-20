# Artifact Registry wrapper (needs only the artifactregistry API from the services workspace).
# The env prefix is applied once here: key "shop" → repository_id "staging-shop".
module "artifact_registry" {
  source = "../../../modules/artifact-registry"

  project_id   = var.project_id
  region       = var.region
  env          = var.env
  repositories = { for k, v in var.repositories : "${var.env}-${k}" => v }
}
