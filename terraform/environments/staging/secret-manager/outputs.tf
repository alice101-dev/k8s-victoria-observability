output "secret_ids" {
  description = "Secret IDs; add a value with: gcloud secrets versions add <id> --data-file=-"
  value       = module.secret_manager.secret_ids
}
