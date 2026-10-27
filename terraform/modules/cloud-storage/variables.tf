variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "Default bucket location (e.g. asia-southeast2 = Jakarta)"
  type        = string
}

variable "env" {
  description = "Environment name, used as a label"
  type        = string
}

variable "buckets" {
  description = "Buckets keyed by their final (globally unique) name."
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
    iam = optional(list(object({
      role   = string
      member = string
    })), [])
  }))
  default = {}
}
