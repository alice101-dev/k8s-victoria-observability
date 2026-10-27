locals {
  image_registry = "${var.region}-docker.pkg.dev/${var.project_id}/${var.env}-${var.image_repository}"
  backup_bucket  = "${var.project_id}-${var.env}-${var.backup_bucket}"
}

module "argocd" {
  source = "../../../modules/argocd-bootstrap"

  argocd_chart_version      = var.argocd_chart_version
  argocd_apps_chart_version = var.argocd_apps_chart_version

  gitops_repo_url        = var.gitops_repo_url
  gitops_target_revision = var.gitops_target_revision
  gitops_repo_username   = var.gitops_repo_username
  gitops_repo_password   = var.gitops_repo_password

  root_application_file = "${path.root}/../../../../bootstrap/root.yaml"

  api_accounts = {
    mcp-argocd = "role:readonly"
  }

  cluster_labels = merge(
    { env = var.env },
    { for name in var.enabled_components : "enable_${name}" => "true" },
  )
  cluster_annotations = {
    "platform/project-id"     = var.project_id
    "platform/image-registry" = local.image_registry
    "platform/backup-bucket"  = local.backup_bucket
  }
}
