variable "project_id" {
  description = "GCP project ID for the repositories."
  type        = string
}

variable "region" {
  description = "Default repository location (e.g. asia-southeast2); override per repo with location."
  type        = string
}

variable "env" {
  description = "Environment name (e.g. staging), used for labels."
  type        = string
}

variable "repositories" {
  description = <<-EOT
    Artifact Registry repositories. Key = repository_id; add/remove a repo by adding/removing a key.

    Example:
      repositories = {
        "app-images" = {
          format         = "DOCKER"
          description    = "Application images"
          immutable_tags = true
          cleanup_policies = {
            "keep-recent" = {
              action               = "KEEP"
              most_recent_versions = { keep_count = 10 }
            }
            "delete-old-untagged" = {
              action    = "DELETE"
              condition = { tag_state = "UNTAGGED", older_than = "2592000s" }
            }
          }
        }
      }
  EOT
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

  validation {
    condition = alltrue([
      for r in values(var.repositories) :
      contains(["DOCKER", "MAVEN", "NPM", "PYTHON", "APT", "YUM", "GO", "KUBEFLOW", "GENERIC"], upper(r.format))
    ])
    error_message = "format must be one of: DOCKER, MAVEN, NPM, PYTHON, APT, YUM, GO, KUBEFLOW, GENERIC."
  }

  validation {
    condition = alltrue([
      for r in values(var.repositories) :
      contains(["STANDARD_REPOSITORY", "REMOTE_REPOSITORY", "VIRTUAL_REPOSITORY"], r.mode)
    ])
    error_message = "mode must be one of: STANDARD_REPOSITORY, REMOTE_REPOSITORY, VIRTUAL_REPOSITORY."
  }

  validation {
    condition = alltrue([
      for r in values(var.repositories) : alltrue([
        for p in values(r.cleanup_policies) : contains(["KEEP", "DELETE"], p.action)
      ])
    ])
    error_message = "cleanup_policies[*].action must be KEEP or DELETE."
  }
}
