output "bucket_urls" {
  description = "gs:// URL per bucket, e.g. gs://<project>-staging-pg-backup"
  value       = module.cloud_storage.bucket_urls
}
