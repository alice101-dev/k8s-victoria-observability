# --- GCP provider ---
# project_id is not stored in the repo: set via TF_VAR_project_id from ../.env (Makefile).
region = "asia-southeast2"
zone   = "asia-southeast2-a"
env    = "staging"

# --- Target cluster (kubernetes-engine workspace) ---
cluster_name     = "observability"     # → staging-observability
cluster_location = "asia-southeast2-a" # zonal

# --- Argo CD (pinned versions) ---
argocd_chart_version      = "10.10.5" # Argo CD v3.5.5
argocd_apps_chart_version = "2.0.7"

# --- GitOps repository ---
# gitops_repo_url comes from TF_VAR_gitops_repo_url (Makefile: ../.env or git remote origin).
# Private repo: export TF_VAR_gitops_repo_password=<GitHub fine-grained PAT, Contents: Read-only>
gitops_target_revision = "main"

# --- Application images ---
# The registry (<region>-docker.pkg.dev/<project>/<env>-<repo>) is built from project_id at
# apply time and injected into the root Application, so the project ID never lands in Git.
image_repository = "shop" # same key as in artifact-registry/staging.tfvars

# --- AppProjects: restrict each domain to its namespaces and chart sources ---
app_projects = {
  observability = {
    description = "VictoriaMetrics/Logs/Traces, Grafana and supporting operators"
    # kube-system: kube-dns scrape Service created by the k8s-stack chart.
    namespaces = ["monitoring", "vm-operator", "external-secrets", "kube-system"]
    source_repos = [
      "https://victoriametrics.github.io/helm-charts/",
      "https://charts.external-secrets.io",
      "https://grafana-community.github.io/helm-charts",
    ]
  }
  shop = {
    description = "Shop application: 6 Go microservices, Redis, PostgreSQL 18 (CloudNativePG)"
    namespaces  = ["shop", "cnpg-system"]
    source_repos = [
      "https://cloudnative-pg.github.io/charts",
    ]
  }
}

# --- Root Application (app-of-apps) ---
root_applications = {
  observability = {
    path    = "observability/argocd"
    project = "observability"
  }
  apps = {
    path    = "apps/argocd"
    project = "shop"
  }
}
