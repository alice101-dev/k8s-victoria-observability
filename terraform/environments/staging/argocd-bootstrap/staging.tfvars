region = "asia-southeast2"
zone   = "asia-southeast2-a"
env    = "staging"

cluster_name     = "observability"
cluster_location = "asia-southeast2-a"

argocd_chart_version      = "10.10.5"
argocd_apps_chart_version = "2.0.7"

gitops_target_revision = "main"

image_repository = "deus"

backup_bucket = "pg-backup"
