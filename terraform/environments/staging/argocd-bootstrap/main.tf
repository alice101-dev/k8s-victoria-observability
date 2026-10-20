# GitOps bootstrap: Argo CD + AppProjects + two root (app-of-apps) Applications:
#   - observability → observability/argocd  (ESO, VM operator, Victoria stack, Grafana, MCP)
#   - apps          → apps/argocd           (CloudNativePG, PostgreSQL 18, Redis, 6 Go services)
# Apply after kubernetes-engine (the cluster must exist).
locals {
  # Image host path contains the project ID, so it is built here instead of in Git.
  image_registry = "${var.region}-docker.pkg.dev/${var.project_id}/${var.env}-${var.image_repository}"
}

module "argocd" {
  source = "../../../modules/argocd-bootstrap"

  argocd_chart_version      = var.argocd_chart_version
  argocd_apps_chart_version = var.argocd_apps_chart_version

  gitops_repo_url        = var.gitops_repo_url
  gitops_target_revision = var.gitops_target_revision
  gitops_repo_username   = var.gitops_repo_username
  gitops_repo_password   = var.gitops_repo_password

  app_projects      = var.app_projects
  root_applications = var.root_applications

  # Passed to every root app-of-apps chart (ignored where unused).
  root_app_values = {
    imageRegistry = local.image_registry
  }
}
