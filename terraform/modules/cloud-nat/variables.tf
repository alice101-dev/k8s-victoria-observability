variable "vpc_id" {
  description = "The self_link or id of the VPC network this NAT is attached to."
  type        = string
}

variable "nat_name" {
  description = "Unique name for this Cloud NAT configuration (used to name router and NAT resources)."
  type        = string
}

variable "region" {
  description = "GCP region for the Cloud Router and NAT (e.g. asia-southeast2)."
  type        = string
}

variable "nat_ip_allocate_option" {
  description = "How NAT IPs are allocated. MANUAL_ONLY = reserved static IPs; AUTO_ONLY = GCP auto-assigns."
  type        = string
  default     = "MANUAL_ONLY"

  validation {
    condition     = contains(["MANUAL_ONLY", "AUTO_ONLY"], var.nat_ip_allocate_option)
    error_message = "nat_ip_allocate_option must be 'MANUAL_ONLY' or 'AUTO_ONLY'."
  }
}

variable "nat_ip_count" {
  description = "Number of reserved static public IPs for Cloud NAT. Only used when nat_ip_allocate_option = MANUAL_ONLY."
  type        = number
  default     = 2

  validation {
    condition     = var.nat_ip_count >= 1
    error_message = "nat_ip_count must be at least 1."
  }
}

variable "source_subnetwork_ip_ranges_to_nat" {
  description = "Which subnets are covered by this NAT. ALL_SUBNETWORKS_ALL_IP_RANGES is the most common."
  type        = string
  default     = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  validation {
    condition = contains([
      "ALL_SUBNETWORKS_ALL_IP_RANGES",
      "ALL_SUBNETWORKS_ALL_PRIMARY_IP_RANGES",
      "LIST_OF_SUBNETWORKS",
    ], var.source_subnetwork_ip_ranges_to_nat)
    error_message = "Invalid source_subnetwork_ip_ranges_to_nat value."
  }
}

variable "enable_dynamic_port_allocation" {
  description = "Enable dynamic port allocation. Recommended true for variable-traffic workloads."
  type        = bool
  default     = true
}

variable "min_ports_per_vm" {
  description = "Minimum ports per VM when dynamic port allocation is enabled."
  type        = number
  default     = 64
}

variable "max_ports_per_vm" {
  description = "Maximum ports per VM when dynamic port allocation is enabled."
  type        = number
  default     = 65536
}

variable "tcp_established_idle_timeout_sec" {
  description = "Timeout (seconds) for established TCP connections. GCP default is 1200."
  type        = number
  default     = 1200
}

variable "tcp_transitory_idle_timeout_sec" {
  description = "Timeout (seconds) for transitory TCP connections. GCP default is 30."
  type        = number
  default     = 30
}

variable "udp_idle_timeout_sec" {
  description = "Timeout (seconds) for UDP connections. GCP default is 30."
  type        = number
  default     = 30
}

variable "icmp_idle_timeout_sec" {
  description = "Timeout (seconds) for ICMP connections. GCP default is 30."
  type        = number
  default     = 30
}

variable "enable_endpoint_independent_mapping" {
  description = "Enable endpoint-independent mapping (RFC 4787). Required for some P2P/gaming workloads."
  type        = bool
  default     = false
}

variable "enable_logging" {
  description = "Enable Cloud NAT logging."
  type        = bool
  default     = true
}

variable "log_filter" {
  description = "Log filter for Cloud NAT. Valid values: ERRORS_ONLY, TRANSLATIONS_ONLY, ALL."
  type        = string
  default     = "ERRORS_ONLY"

  validation {
    condition     = contains(["ERRORS_ONLY", "TRANSLATIONS_ONLY", "ALL"], var.log_filter)
    error_message = "log_filter must be one of: ERRORS_ONLY, TRANSLATIONS_ONLY, ALL."
  }
}

variable "env" {
  description = "Environment prefix applied to all resource names (e.g. prod, staging)."
  type        = string
}
