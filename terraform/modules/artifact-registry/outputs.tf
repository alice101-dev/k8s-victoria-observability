output "repository_ids" {
  description = "Logical key → repository_id."
  value       = { for k, v in google_artifact_registry_repository.repo : k => v.repository_id }
}

output "repository_names" {
  description = "Logical key → full resource name (projects/<p>/locations/<loc>/repositories/<id>)."
  value       = { for k, v in google_artifact_registry_repository.repo : k => v.name }
}

output "registry_uris" {
  description = "Logical key → registry URI (image host path, e.g. asia-southeast2-docker.pkg.dev/<project>/<repo>)."
  value       = { for k, v in google_artifact_registry_repository.repo : k => v.registry_uri }
}
