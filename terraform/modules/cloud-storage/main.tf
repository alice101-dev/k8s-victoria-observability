# Cloud Storage buckets, map-driven (one map key per bucket): uniform access, public access
# prevention, lifecycle rules and bucket-level IAM. No deletion guard (force_destroy): test environment.

locals {
  labels = {
    env        = var.env
    managed_by = "terraform"
  }

  # One entry per (bucket, role, member) for google_storage_bucket_iam_member.
  bucket_iam = merge([
    for bucket, b in var.buckets : {
      for binding in b.iam : "${bucket}|${binding.role}|${binding.member}" => merge(binding, { bucket = bucket })
    }
  ]...)
}

resource "google_storage_bucket" "bucket" {
  for_each = var.buckets

  # checkov:skip=CKV_GCP_62: access logs are not needed for a backup bucket in the test environment
  # checkov:skip=CKV_GCP_78: versioning is per bucket (versioning attribute); Barman manages backup retention itself

  project       = var.project_id
  name          = each.key
  location      = coalesce(each.value.location, var.region)
  storage_class = each.value.storage_class
  force_destroy = each.value.force_destroy

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  labels = merge(local.labels, each.value.labels)

  versioning {
    enabled = each.value.versioning
  }

  # 0 disables soft delete: deleted objects are gone immediately (and stop costing).
  soft_delete_policy {
    retention_duration_seconds = each.value.soft_delete_retention_seconds
  }

  dynamic "lifecycle_rule" {
    for_each = each.value.lifecycle_rules
    content {
      action {
        type = lifecycle_rule.value.action
      }
      condition {
        age = lifecycle_rule.value.age_days
      }
    }
  }
}

resource "google_storage_bucket_iam_member" "member" {
  for_each = local.bucket_iam

  bucket = google_storage_bucket.bucket[each.value.bucket].name
  role   = each.value.role
  member = each.value.member
}
