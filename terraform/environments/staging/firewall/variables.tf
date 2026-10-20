# --- GCP provider ---
variable "project_id" {
  description = "GCP project ID"
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

# --- VPC & Networking ---
variable "network_vpc_name" {
  description = "Base VPC name (without env prefix)"
  type        = string
}

variable "firewall_rules" {
  description = "Map of firewall rules to create in the VPC"
  type = map(object({
    direction          = optional(string, "INGRESS")
    priority           = optional(number, 1000)
    source_ranges      = optional(list(string))
    destination_ranges = optional(list(string))
    source_tags        = optional(list(string))
    target_tags        = optional(list(string))
    allow = optional(list(object({
      protocol = string
      ports    = optional(list(string))
    })), [])
    deny = optional(list(object({
      protocol = string
      ports    = optional(list(string))
    })), [])
    description = optional(string)
    disabled    = optional(bool, false)
    log_config = optional(object({
      metadata = optional(string, "INCLUDE_ALL_METADATA")
    }))
  }))
  default = {}
}
