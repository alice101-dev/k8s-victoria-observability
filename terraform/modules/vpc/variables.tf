variable "network_vpc_name" {
  description = "VPC name, already env-prefixed (e.g. staging-vpc-observability)"
  type        = string
}

variable "subnets" {
  description = "Subnets to create. Key = subnet name suffix."
  type = map(object({
    ip_cidr_range            = string
    region                   = string
    private_ip_google_access = optional(bool, true)
  }))
  default = {}

  validation {
    condition     = alltrue([for s in values(var.subnets) : can(cidrhost(s.ip_cidr_range, 0))])
    error_message = "subnets: ip_cidr_range must be a valid CIDR (e.g. 10.120.0.0/20)."
  }
}
