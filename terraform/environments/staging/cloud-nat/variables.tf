variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "Default GCP region (used by provider)."
  type        = string
}

variable "zone" {
  description = "Default GCP zone (used by provider)."
  type        = string
}

variable "env" {
  description = "Environment prefix applied to all resource names (e.g. prod, staging)."
  type        = string
}

variable "cloud_nat_configs" {
  description = "Cloud NAT configurations for the VPC. Each entry creates one router, one NAT and N static IPs."
  type = map(object({
    vpc_name = string

    region = string

    nat_ip_allocate_option = optional(string, "MANUAL_ONLY")
    nat_ip_count           = optional(number, 2)

    source_subnetwork_ip_ranges_to_nat = optional(string, "ALL_SUBNETWORKS_ALL_IP_RANGES")

    enable_dynamic_port_allocation = optional(bool, true)
    min_ports_per_vm               = optional(number, 64)
    max_ports_per_vm               = optional(number, 65536)

    tcp_established_idle_timeout_sec = optional(number, 1200)
    tcp_transitory_idle_timeout_sec  = optional(number, 30)
    udp_idle_timeout_sec             = optional(number, 30)
    icmp_idle_timeout_sec            = optional(number, 30)

    enable_endpoint_independent_mapping = optional(bool, false)

    enable_logging = optional(bool, true)
    log_filter     = optional(string, "ERRORS_ONLY")
  }))

  validation {
    condition = alltrue([
      for k, v in var.cloud_nat_configs :
      can(regex("^[a-z][a-z0-9-]+$", k))
    ])
    error_message = "cloud_nat_configs keys must be lowercase alphanumeric with hyphens (e.g. 'vpc-observability-main')."
  }
}
