locals {
  argocd_values = {
    global = {
      domain = "localhost:8080"
    }
    configs = {
      params = {
        "server.insecure" = true
      }
      cm = merge({
        "timeout.reconciliation" = "180s"
        "admin.enabled"          = true
      }, { for name in keys(var.api_accounts) : "accounts.${name}" => "apiKey" })
      rbac = {
        "policy.csv" = join("\n", [for name, role in var.api_accounts : "g, ${name}, ${role}"])
      }
      clusterCredentials = {
        in-cluster = {
          server      = "https://kubernetes.default.svc"
          labels      = var.cluster_labels
          annotations = var.cluster_annotations
          config = {
            tlsClientConfig = { insecure = false }
          }
        }
      }
    }
    dex            = { enabled = false }
    notifications  = { enabled = false }
    applicationSet = { replicas = 1, metrics = { enabled = true } }
    controller = {
      replicas  = 1
      metrics   = { enabled = true }
      resources = { requests = { cpu = "100m", memory = "512Mi" }, limits = { memory = "1Gi" } }
    }
    repoServer = {
      replicas  = 1
      metrics   = { enabled = true }
      resources = { requests = { cpu = "25m", memory = "128Mi" }, limits = { memory = "512Mi" } }
    }
    server = {
      replicas  = 1
      metrics   = { enabled = true }
      resources = { requests = { cpu = "25m", memory = "64Mi" }, limits = { memory = "256Mi" } }
    }
    redis = {
      resources = { requests = { cpu = "10m", memory = "32Mi" }, limits = { memory = "128Mi" } }
    }
  }

  repo_credentials = var.gitops_repo_password == null ? [] : [
    { name = "configs.repositories.gitops.url", value = var.gitops_repo_url },
    { name = "configs.repositories.gitops.type", value = "git" },
    { name = "configs.repositories.gitops.username", value = var.gitops_repo_username },
    { name = "configs.repositories.gitops.password", value = var.gitops_repo_password },
  ]

  root = yamldecode(templatefile(var.root_application_file, {
    repo_url = var.gitops_repo_url
    revision = var.gitops_target_revision
  }))
}

resource "helm_release" "argocd" {
  name             = "argocd"
  namespace        = var.namespace
  create_namespace = true
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = var.argocd_chart_version

  values        = [yamlencode(local.argocd_values)]
  set_sensitive = local.repo_credentials

  wait    = true
  timeout = 600
}

resource "helm_release" "root" {
  name       = "argocd-apps"
  namespace  = var.namespace
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argocd-apps"
  version    = var.argocd_apps_chart_version

  values = [yamlencode({
    applications = {
      (local.root.metadata.name) = merge(local.root.spec, {
        namespace  = local.root.metadata.namespace
        finalizers = local.root.metadata.finalizers
      })
    }
  })]

  depends_on = [helm_release.argocd]
}
