region = "asia-southeast2"
zone   = "asia-southeast2-a"
env    = "staging"

buckets = {
  "pg-backup" = {
    soft_delete_retention_seconds = 0
    lifecycle_rules = [
      { action = "Delete", age_days = 30 },
      { action = "AbortIncompleteMultipartUpload", age_days = 1 },
    ]
    workload_identity = [
      { namespace = "deus", service_account = "deus-db", role = "roles/storage.objectAdmin" },
      { namespace = "deus", service_account = "deus-db", role = "roles/storage.legacyBucketReader" },
      { namespace = "deus", service_account = "deus-db-restore", role = "roles/storage.objectViewer" },
      { namespace = "deus", service_account = "deus-db-restore", role = "roles/storage.legacyBucketReader" },
    ]
  }
}
