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

variable "enabled_apis" {
  description = "Google Cloud APIs to enable in the project"
  type        = list(string)
}

variable "disable_on_destroy" {
  description = "Disable APIs on destroy. Keep false so destroying this workspace never turns off APIs in use."
  type        = bool
  default     = false
}
