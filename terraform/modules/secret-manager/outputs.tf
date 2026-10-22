output "secret_ids" {
  description = "Secret IDs, e.g. staging-alertmanager-slack-webhook"
  value       = [for s in google_secret_manager_secret.secret : s.secret_id]
}
