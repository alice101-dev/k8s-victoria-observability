# --- GCP provider ---
variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "GCP region, also the replica location of the secrets (e.g. asia-southeast2 = Jakarta)"
  type        = string
}

variable "zone" {
  description = "GCP zone (provider only)"
  type        = string
}

variable "env" {
  description = "Environment name, prefix of the secret IDs and a label"
  type        = string
}

# --- Secrets ---
variable "secrets" {
  description = "Secrets keyed by name (without the <env>- prefix); workload_identity grants roles to Kubernetes ServiceAccounts."
  type = map(object({
    labels = optional(map(string), {})
    workload_identity = optional(list(object({
      namespace       = string
      service_account = string
      role            = string
    })), [])
  }))
  default = {}
}
