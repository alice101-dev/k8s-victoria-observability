output "namespace" {
  description = "Argo CD namespace"
  value       = helm_release.argocd.namespace
}

output "argocd_version" {
  description = "Installed Argo CD version"
  value       = helm_release.argocd.metadata.app_version
}
