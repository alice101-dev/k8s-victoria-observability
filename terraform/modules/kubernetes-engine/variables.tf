variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "env" {
  description = "Environment name (e.g. staging)"
  type        = string
}

variable "location" {
  description = "Cluster location: a zone (zonal, e.g. asia-southeast2-a) or a region (regional, e.g. asia-southeast2)"
  type        = string
}

variable "cluster_name" {
  description = "Cluster name, already env-prefixed (e.g. staging-observability)"
  type        = string
}

variable "vpc_id" {
  description = "VPC self-link ID"
  type        = string
}

variable "subnet_id" {
  description = "Node subnet self-link ID"
  type        = string
}

variable "master_ipv4_cidr_block" {
  description = "Internal /28 range for the control plane (must not overlap other subnets)"
  type        = string

  validation {
    condition     = can(cidrhost(var.master_ipv4_cidr_block, 0)) && endswith(var.master_ipv4_cidr_block, "/28")
    error_message = "master_ipv4_cidr_block must be a /28 CIDR (e.g. 10.0.3.0/28)."
  }
}

variable "cluster_ipv4_cidr_block" {
  description = "Pod IP range (e.g. /16 for an automatic range, or an explicit CIDR)"
  type        = string
  default     = "/16"
}

variable "services_ipv4_cidr_block" {
  description = "Service IP range (e.g. /22 for an automatic range, or an explicit CIDR)"
  type        = string
  default     = "/22"
}

variable "release_channel" {
  description = "GKE release channel: RAPID, REGULAR or STABLE"
  type        = string
  default     = "STABLE"

  validation {
    condition     = contains(["RAPID", "REGULAR", "STABLE"], var.release_channel)
    error_message = "release_channel must be RAPID, REGULAR or STABLE."
  }
}

variable "master_authorized_networks" {
  description = "CIDR allowlist for the control plane IP endpoint (the IAM-gated DNS endpoint is not affected)"
  type = list(object({
    cidr_block   = string
    display_name = string
  }))
  default = []
}

variable "logging_components" {
  description = "Components whose logs go to Cloud Logging (workloads use VictoriaLogs)"
  type        = list(string)
  default     = ["SYSTEM_COMPONENTS"]
}

variable "monitoring_components" {
  description = "Components whose metrics go to Cloud Monitoring (workloads use VictoriaMetrics)"
  type        = list(string)
  default     = ["SYSTEM_COMPONENTS"]
}

variable "maintenance_start_time" {
  description = "Maintenance window start, RFC3339 UTC ending in 'Z'"
  type        = string
  default     = "2026-01-04T18:00:00Z"

  validation {
    condition     = endswith(var.maintenance_start_time, "Z")
    error_message = "maintenance_start_time must be UTC and end in 'Z' to avoid a permanent diff."
  }
}

variable "maintenance_recurrence" {
  description = "Maintenance window RRULE (days in UTC)"
  type        = string
  default     = "FREQ=WEEKLY;BYDAY=SU,MO,TU,WE"
}

variable "sa_account_id" {
  description = "Node service account account_id (6-30 characters)"
  type        = string
}

variable "sa_display_name" {
  description = "Node service account display name"
  type        = string
  default     = ""
}

variable "sa_roles" {
  description = "Minimal IAM roles for the node service account"
  type        = list(string)
  default = [
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/stackdriver.resourceMetadata.writer",
    "roles/artifactregistry.reader",
  ]

  validation {
    condition     = alltrue([for r in var.sa_roles : !contains(["roles/owner", "roles/editor"], r)])
    error_message = "The node service account must not get roles/owner or roles/editor."
  }
}

variable "node_pools" {
  description = "Node pools keyed by name suffix. All pools must be Spot VMs (spot = true)."
  type = map(object({
    machine_type   = optional(string, "e2-standard-2")
    min_node_count = optional(number, 1)
    max_node_count = optional(number, 3)
    spot           = optional(bool, true)
    node_locations = optional(list(string))
    disk_size_gb   = optional(number, 30)
    disk_type      = optional(string, "pd-balanced")
    labels         = optional(map(string), {})
    tags           = optional(list(string), [])
  }))

  validation {
    condition     = alltrue([for p in values(var.node_pools) : p.spot])
    error_message = "All node pools must be Spot VMs (spot = true)."
  }

  validation {
    condition     = alltrue([for p in values(var.node_pools) : p.min_node_count <= p.max_node_count])
    error_message = "min_node_count must not exceed max_node_count."
  }
}
