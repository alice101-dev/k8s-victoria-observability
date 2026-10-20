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
  description = "Primary GCP zone (zonal cluster and node location)"
  type        = string
}

variable "env" {
  description = "Environment name (e.g. staging)"
  type        = string
}

# --- VPC & Networking ---
variable "network_vpc_name" {
  description = "Base VPC name from the vpc workspace (without env prefix)"
  type        = string
}

variable "subnet_key" {
  description = "Subnet key in the vpc workspace used by GKE nodes"
  type        = string
  default     = "gke"
}

# --- GKE Cluster ---
variable "cluster_name" {
  description = "Base cluster name (without env prefix)"
  type        = string
}

variable "regional_cluster" {
  description = "false = zonal cluster in var.zone (single zone, cheapest); true = regional (3-zone control plane)"
  type        = bool
  default     = false
}

variable "master_ipv4_cidr_block" {
  description = "Control plane /28 range"
  type        = string
}

variable "cluster_ipv4_cidr_block" {
  description = "Pod IP range"
  type        = string
}

variable "services_ipv4_cidr_block" {
  description = "Service IP range"
  type        = string
}

variable "master_authorized_networks" {
  description = "CIDR allowlist for the control plane IP endpoint (the DNS endpoint is not affected)"
  type = list(object({
    cidr_block   = string
    display_name = string
  }))
  default = []
}

variable "release_channel" {
  description = "GKE release channel (RAPID, REGULAR, STABLE)"
  type        = string
  default     = "STABLE"
}

variable "maintenance_start_time" {
  description = "Maintenance window start (UTC, ending in Z)"
  type        = string
}

variable "maintenance_recurrence" {
  description = "Maintenance window RRULE (UTC days)"
  type        = string
}

variable "sa_account_id" {
  description = "Base node service account account_id (without env prefix)"
  type        = string
}

variable "sa_display_name" {
  description = "Node service account display name"
  type        = string
  default     = ""
}

variable "node_pools" {
  description = "GKE node pools (Spot VMs only)"
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
}
