variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "Region the secrets are replicated to (user-managed replication)"
  type        = string
}

variable "env" {
  description = "Environment name, used as a label"
  type        = string
}

variable "secrets" {
  description = "Secrets keyed by secret ID; iam grants roles to members (e.g. principal:// Kubernetes ServiceAccounts)."
  type = map(object({
    labels = optional(map(string), {})
    iam = optional(list(object({
      role   = string
      member = string
    })), [])
  }))
  default = {}
}
