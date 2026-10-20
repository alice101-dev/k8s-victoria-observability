variable "network_vpc_name" {
  description = "VPC name, already env-prefixed (e.g. staging-vpc-observability)"
  type        = string
}

variable "vpc_id" {
  description = "The self-link ID of the VPC network to apply firewall rules to"
  type        = string
}

variable "firewall_rules" {
  description = "Firewall rules to create, keyed by rule name suffix"
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

  # Defense in depth for what checkov cannot scan: reject 0.0.0.0/0 ingress on SSH (22)
  # or RDP (3389).
  validation {
    condition = alltrue([
      for k, r in var.firewall_rules :
      !(
        r.direction == "INGRESS" &&
        contains((r.source_ranges == null ? [] : r.source_ranges), "0.0.0.0/0") &&
        anytrue([
          for a in r.allow :
          contains((a.ports == null ? [] : a.ports), "22") ||
          contains((a.ports == null ? [] : a.ports), "3389")
        ])
      )
    ])
    error_message = "Firewall: INGRESS from 0.0.0.0/0 must not open SSH (22) or RDP (3389). Restrict source_ranges (e.g. the IAP range 35.235.240.0/20)."
  }
}
