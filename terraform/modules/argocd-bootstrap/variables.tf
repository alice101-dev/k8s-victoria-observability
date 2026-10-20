variable "namespace" {
  description = "Argo CD namespace"
  type        = string
  default     = "argocd"
}

variable "argocd_chart_version" {
  description = "argo/argo-cd chart version (pinned; upgrade via PR)"
  type        = string
}

variable "argocd_apps_chart_version" {
  description = "argo/argocd-apps chart version (pinned)"
  type        = string
}

variable "gitops_repo_url" {
  description = "HTTPS URL of the GitOps repository (this repo), e.g. https://github.com/<org>/k8s-victoria-observability.git"
  type        = string

  validation {
    condition     = can(regex("^https://", var.gitops_repo_url)) && !strcontains(var.gitops_repo_url, "CHANGE-ME")
    error_message = "gitops_repo_url must be the HTTPS URL of the Git repository (set GITOPS_REPO_URL in .env or a git remote origin)."
  }
}

variable "gitops_target_revision" {
  description = "Branch, tag or commit synced by Argo CD"
  type        = string
  default     = "main"
}

variable "gitops_repo_username" {
  description = "Private repo username (any value for GitHub, e.g. \"git\"). Ignored when gitops_repo_password is null."
  type        = string
  default     = "git"
}

variable "gitops_repo_password" {
  description = "Read-only token for a private repo (GitHub fine-grained PAT, Contents: Read). null = public repo. Set via TF_VAR_gitops_repo_password, never in tfvars."
  type        = string
  default     = null
  sensitive   = true
}

variable "app_projects" {
  description = "Argo CD AppProjects per domain. Key = project name."
  type = map(object({
    description  = string
    namespaces   = list(string)
    source_repos = optional(list(string), [])
  }))
}

variable "root_app_values" {
  description = "Extra valuesObject entries for every root Application (environment data kept out of Git, e.g. imageRegistry)"
  type        = map(string)
  default     = {}
}

variable "root_applications" {
  description = "Root (app-of-apps) Applications. Key = Application name; path = app-of-apps chart folder; project = AppProject for child apps."
  type = map(object({
    path    = string
    project = string
  }))
}
