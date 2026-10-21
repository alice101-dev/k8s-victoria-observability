# --- GCP provider ---
variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "Primary GCP region"
  type        = string
}

variable "zone" {
  description = "Primary GCP zone"
  type        = string
}

variable "env" {
  description = "Environment name (e.g. staging)"
  type        = string
}

# --- Target cluster (looked up by name) ---
variable "cluster_name" {
  description = "Base cluster name (without env prefix), as in the kubernetes-engine workspace"
  type        = string
}

variable "cluster_location" {
  description = "Cluster location: zone (zonal) or region (regional)"
  type        = string
}

# --- Argo CD ---
variable "argocd_chart_version" {
  description = "argo/argo-cd chart version"
  type        = string
}

variable "argocd_apps_chart_version" {
  description = "argo/argocd-apps chart version"
  type        = string
}

variable "gitops_repo_url" {
  description = "HTTPS URL of the GitOps repository (set via TF_VAR_gitops_repo_url; the Makefile reads .env or git remote origin)"
  type        = string
}

variable "image_repository" {
  description = "Base Artifact Registry repository name (without env prefix), as in the artifact-registry workspace"
  type        = string
}

variable "backup_bucket" {
  description = "PostgreSQL backup bucket key (without <project>-<env>- prefix), as in the cloud-storage workspace"
  type        = string
}

variable "gitops_target_revision" {
  description = "Branch or tag synced by Argo CD"
  type        = string
  default     = "main"
}

variable "gitops_repo_username" {
  description = "Private repo username (any value for GitHub, e.g. \"git\")"
  type        = string
  default     = "git"
}

variable "gitops_repo_password" {
  description = "Read-only token for a private repo (export TF_VAR_gitops_repo_password=<PAT>). null = public repo."
  type        = string
  default     = null
  sensitive   = true
}

variable "app_projects" {
  description = "Argo CD AppProjects per domain"
  type = map(object({
    description  = string
    namespaces   = list(string)
    source_repos = optional(list(string), [])
  }))
}

variable "root_applications" {
  description = "Root (app-of-apps) Applications"
  type = map(object({
    path    = string
    project = string
  }))
}
