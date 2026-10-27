variable "namespace" {
  description = "Argo CD namespace"
  type        = string
  default     = "argocd"
}

variable "argocd_chart_version" {
  description = "argo/argo-cd chart version"
  type        = string
}

variable "argocd_apps_chart_version" {
  description = "argo/argocd-apps chart version"
  type        = string
}

variable "gitops_repo_url" {
  description = "HTTPS URL of the platform GitOps repository"
  type        = string

  validation {
    condition     = can(regex("^https://", var.gitops_repo_url)) && !strcontains(var.gitops_repo_url, "CHANGE-ME")
    error_message = "gitops_repo_url must be the HTTPS URL of the Git repository (set GITOPS_REPO_URL in .env or a git remote origin)."
  }
}

variable "gitops_target_revision" {
  description = "Branch, tag or commit synced by the root Application"
  type        = string
  default     = "main"
}

variable "gitops_repo_username" {
  description = "Private repo username, ignored when gitops_repo_password is null"
  type        = string
  default     = "git"
}

variable "gitops_repo_password" {
  description = "Read-only token for a private repo, null for a public repo; set via TF_VAR_gitops_repo_password"
  type        = string
  default     = null
  sensitive   = true
}

variable "root_application_file" {
  description = "Path of the root Application manifest (bootstrap/root.yaml), rendered with repo_url and revision"
  type        = string
}

variable "cluster_labels" {
  description = "Labels of the in-cluster Argo CD cluster secret, matched by the ApplicationSet cluster generators"
  type        = map(string)
}

variable "cluster_annotations" {
  description = "Annotations of the in-cluster Argo CD cluster secret: environment values kept out of Git"
  type        = map(string)
  default     = {}
}

variable "api_accounts" {
  description = "Local Argo CD accounts that may only create API tokens, mapped to their RBAC role"
  type        = map(string)
  default     = {}
}
