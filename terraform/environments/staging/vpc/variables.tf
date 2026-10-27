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

variable "network_vpc_name" {
  description = "Base VPC name (without env prefix; added by the wrapper)"
  type        = string
}

variable "subnets" {
  description = "Subnets to create in the VPC"
  type = map(object({
    ip_cidr_range            = string
    region                   = string
    private_ip_google_access = optional(bool, true)
  }))
  default = {}
}
