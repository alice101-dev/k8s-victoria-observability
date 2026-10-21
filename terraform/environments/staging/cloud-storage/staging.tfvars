# --- GCP provider ---
# project_id is not stored in the repo: set via TF_VAR_project_id from ../.env (Makefile).
region = "asia-southeast2" # Jakarta
zone   = "asia-southeast2-a"
env    = "staging"

# --- Buckets ---
# Keys without prefix; main.tf names them "<project>-<env>-<key>" (bucket names are global).
buckets = {
  # CloudNativePG base backups and WAL archive (Barman Cloud plugin, ObjectStore "shop-backup").
  # Barman enforces the retention (7d); the Delete rule is only a safety net.
  "pg-backup" = {
    # Staging: deleted objects are removed at once. Production: keep the 7-day soft delete
    # (protection against accidental or malicious deletion) and a separate project for backups.
    soft_delete_retention_seconds = 0
    lifecycle_rules = [
      { action = "Delete", age_days = 30 },
      { action = "AbortIncompleteMultipartUpload", age_days = 1 },
    ]
    # PostgreSQL instance pods run as the Kubernetes ServiceAccount named after the Cluster.
    workload_identity = [
      { namespace = "shop", service_account = "shop-db", role = "roles/storage.objectAdmin" },
      { namespace = "shop", service_account = "shop-db", role = "roles/storage.legacyBucketReader" },
    ]
  }
}
