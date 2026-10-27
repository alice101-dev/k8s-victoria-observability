variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "GCP region, also the default bucket location (e.g. asia-southeast2 = Jakarta)"
  type        = string
}

variable "zone" {
  description = "GCP zone (provider only; buckets are regional)"
  type        = string
}

variable "env" {
  description = "Environment name, part of the bucket names and used as a label"
  type        = string
}

variable "buckets" {
  description = "Buckets keyed by name (without the <project>-<env>- prefix); workload_identity grants roles to Kubernetes ServiceAccounts."
  type = map(object({
    location                      = optional(string)
    storage_class                 = optional(string, "STANDARD")
    force_destroy                 = optional(bool, true)
    versioning                    = optional(bool, false)
    soft_delete_retention_seconds = optional(number, 604800)
    labels                        = optional(map(string), {})
    lifecycle_rules = optional(list(object({
      action   = string
      age_days = number
    })), [])
    workload_identity = optional(list(object({
      namespace       = string
      service_account = string
      role            = string
    })), [])
  }))
  default = {}
}
