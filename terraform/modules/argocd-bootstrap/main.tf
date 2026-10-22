# The only thing Terraform installs in the cluster: Argo CD, AppProjects and the root
# (app-of-apps) Applications. Everything else is managed by GitOps from the repository.

locals {
  # Restore the Application health check (removed in Argo CD 1.8) so app-of-apps sync waves
  # wait for child apps to be healthy (e.g. operators and CRDs before their custom resources).
  # A new child app reports Healthy before its first sync, so anything not Synced is
  # treated as Progressing; otherwise the wave gate is skipped.
  application_health_lua = <<-EOT
    hs = {}
    hs.status = "Progressing"
    hs.message = ""
    if obj.status ~= nil then
      if obj.status.health ~= nil then
        hs.status = obj.status.health.status
        if obj.status.health.message ~= nil then
          hs.message = obj.status.health.message
        end
      end
      if hs.status == "Healthy" and (obj.status.sync == nil or obj.status.sync.status ~= "Synced") then
        hs.status = "Progressing"
        hs.message = "Waiting for first successful sync"
      end
    end
    return hs
  EOT

  argocd_values = {
    global = {
      domain = "localhost:8080"
    }
    configs = {
      params = {
        # UI only via kubectl port-forward (no Ingress/LB), so no TLS on the server.
        "server.insecure" = true
      }
      cm = {
        "resource.customizations.health.argoproj.io_Application" = local.application_health_lua
        "timeout.reconciliation"                                 = "180s"
        "admin.enabled"                                          = true
      }
    }
    # Disable unused components to keep the footprint small.
    dex            = { enabled = false }
    notifications  = { enabled = false }
    applicationSet = { replicas = 1 }
    controller = {
      replicas  = 1
      metrics   = { enabled = true }
      # Request = measured peak (~520Mi) so the scheduler accounts for it.
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

  # Optional private repo credentials, passed via set_sensitive so they stay out of plans.
  repo_credentials = var.gitops_repo_password == null ? [] : [
    { name = "configs.repositories.gitops.url", value = var.gitops_repo_url },
    { name = "configs.repositories.gitops.type", value = "git" },
    { name = "configs.repositories.gitops.username", value = var.gitops_repo_username },
    { name = "configs.repositories.gitops.password", value = var.gitops_repo_password },
  ]

  projects = {
    for name, p in var.app_projects : name => {
      namespace   = var.namespace
      finalizers  = ["resources-finalizer.argocd.argoproj.io"]
      description = p.description
      sourceRepos = concat([var.gitops_repo_url], p.source_repos)
      destinations = [
        for ns in p.namespaces : { namespace = ns, server = "https://kubernetes.default.svc" }
      ]
      # Operators need cluster-scoped resources (CRDs, ClusterRoles, webhooks).
      clusterResourceWhitelist = [{ group = "*", kind = "*" }]
    }
  }

  applications = {
    for name, a in var.root_applications : name => {
      namespace  = var.namespace
      finalizers = ["resources-finalizer.argocd.argoproj.io"]
      project    = "default"
      source = {
        repoURL        = var.gitops_repo_url
        targetRevision = var.gitops_target_revision
        path           = a.path
        helm = {
          # Repo, revision and environment values (e.g. the project-specific image registry)
          # are passed to the app-of-apps charts, so nothing is hardcoded in Git.
          valuesObject = merge(var.root_app_values, {
            repoURL        = var.gitops_repo_url
            targetRevision = var.gitops_target_revision
            project        = a.project
          })
        }
      }
      destination = {
        server    = "https://kubernetes.default.svc"
        namespace = var.namespace
      }
      syncPolicy = {
        automated = { prune = true, selfHeal = true }
        retry = {
          limit   = 10
          backoff = { duration = "30s", factor = 2, maxDuration = "5m" }
        }
      }
    }
  }
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

resource "helm_release" "argocd_apps" {
  name       = "argocd-apps"
  namespace  = var.namespace
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argocd-apps"
  version    = var.argocd_apps_chart_version

  values = [yamlencode({
    projects     = local.projects
    applications = local.applications
  })]

  depends_on = [helm_release.argocd]
}
