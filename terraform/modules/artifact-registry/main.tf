# Artifact Registry repositories, map-driven (one map key per repo): cleanup policies,
# immutable tags and vulnerability scanning. No deletion guard: test environment.

locals {
  labels = {
    env        = var.env
    managed_by = "terraform"
  }
}

resource "google_artifact_registry_repository" "repo" {
  for_each = var.repositories

  # checkov:skip=CKV_GCP_84: CMEK optional via per-repo kms_key_name; Google-managed keys are sufficient here

  project       = var.project_id
  repository_id = each.key
  location      = coalesce(each.value.location, var.region)
  format        = upper(each.value.format)
  mode          = each.value.mode
  description   = each.value.description
  kms_key_name  = each.value.kms_key_name

  labels = merge(local.labels, each.value.labels)


  # cleanup_policy_dry_run = true: policies only report, nothing is deleted.
  cleanup_policy_dry_run = each.value.cleanup_policy_dry_run

  # docker_config applies to DOCKER repositories only (immutable tags).
  dynamic "docker_config" {
    for_each = each.value.immutable_tags != null ? [each.value.immutable_tags] : []
    content {
      immutable_tags = docker_config.value
    }
  }

  # Vulnerability scanning: INHERITED | ENABLED | DISABLED.
  dynamic "vulnerability_scanning_config" {
    for_each = each.value.vulnerability_scanning != null ? [each.value.vulnerability_scanning] : []
    content {
      enablement_config = vulnerability_scanning_config.value
    }
  }

  # Cleanup policies (map key = policy name).
  dynamic "cleanup_policies" {
    for_each = each.value.cleanup_policies
    content {
      id     = cleanup_policies.key
      action = cleanup_policies.value.action

      # KEEP/DELETE by age, prefix or tag state.
      dynamic "condition" {
        for_each = cleanup_policies.value.condition != null ? [cleanup_policies.value.condition] : []
        content {
          tag_state             = condition.value.tag_state
          tag_prefixes          = condition.value.tag_prefixes
          version_name_prefixes = condition.value.version_name_prefixes
          package_name_prefixes = condition.value.package_name_prefixes
          older_than            = condition.value.older_than
          newer_than            = condition.value.newer_than
        }
      }

      # Keep the N most recent versions per package (usually with action = KEEP).
      dynamic "most_recent_versions" {
        for_each = cleanup_policies.value.most_recent_versions != null ? [cleanup_policies.value.most_recent_versions] : []
        content {
          package_name_prefixes = most_recent_versions.value.package_name_prefixes
          keep_count            = most_recent_versions.value.keep_count
        }
      }
    }
  }
}
