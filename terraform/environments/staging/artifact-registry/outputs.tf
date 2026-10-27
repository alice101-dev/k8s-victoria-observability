output "registry_uris" {
  description = "Image host path per repository, e.g. asia-southeast2-docker.pkg.dev/<project>/staging-deus"
  value       = module.artifact_registry.registry_uris
}
