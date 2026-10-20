output "argocd_version" {
  description = "Installed Argo CD version"
  value       = module.argocd.argocd_version
}

output "argocd_ui" {
  description = "How to open the Argo CD UI (no Ingress/LB)"
  value       = "kubectl -n ${module.argocd.namespace} port-forward svc/argocd-server 8080:80  →  http://localhost:8080 (user: admin, password: kubectl -n ${module.argocd.namespace} get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)"
}
