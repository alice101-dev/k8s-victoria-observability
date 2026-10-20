output "enabled_api_ids" {
  description = "Map of enabled API service names to their resource IDs."
  value       = { for k, v in google_project_service.enabled_apis : k => v.id }
}
