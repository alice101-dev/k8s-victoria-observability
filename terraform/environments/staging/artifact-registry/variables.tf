# --- GCP provider ---
variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "GCP region, also the default repository location (e.g. asia-southeast2 = Jakarta)"
  type        = string
}

variable "zone" {
  description = "GCP zone (provider only; Artifact Registry is regional)"
  type        = string
}

variable "env" {
  description = "Environment name, prefixed to repository_id and used as a label"
  type        = string
}

# --- Repositories ---
variable "repositories" {
  description = "Artifact Registry repositories keyed by name (without env prefix); mirrors the module type."
  type = map(object({
    format                 = string
    location               = optional(string)
    mode                   = optional(string, "STANDARD_REPOSITORY")
    description            = optional(string, "")
    kms_key_name           = optional(string)
    labels                 = optional(map(string), {})
    immutable_tags         = optional(bool)
    vulnerability_scanning = optional(string)
    cleanup_policy_dry_run = optional(bool, false)
    cleanup_policies = optional(map(object({
      action = string
      condition = optional(object({
        tag_state             = optional(string)
        tag_prefixes          = optional(list(string))
        version_name_prefixes = optional(list(string))
        package_name_prefixes = optional(list(string))
        older_than            = optional(string)
        newer_than            = optional(string)
      }))
      most_recent_versions = optional(object({
        package_name_prefixes = optional(list(string))
        keep_count            = optional(number)
      }))
    })), {})
  }))
  default = {}
}
