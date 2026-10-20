variable "project_id" {
  description = "GCP Project ID where APIs will be enabled."
  type        = string
}

variable "enabled_apis" {
  description = "List of Google Cloud API service names to enable in the project."
  type        = list(string)
}

variable "disable_on_destroy" {
  description = "Whether to disable the API when the resource is destroyed. Set to false to avoid accidental service disruption."
  type        = bool
  default     = false
}
