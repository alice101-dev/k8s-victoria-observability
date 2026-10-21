output "bucket_urls" {
  description = "gs:// URL per bucket"
  value       = { for name, b in google_storage_bucket.bucket : name => b.url }
}
