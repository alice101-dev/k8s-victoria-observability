# Secret Manager secrets, map-driven (one map key per secret) with secret-level IAM.
# Terraform creates the secrets, never their values: versions are added out of band (gcloud), so no
# secret value lands in Git or in the Terraform state.

locals {
  labels = {
    env        = var.env
    managed_by = "terraform"
  }

  # One entry per (secret, role, member) for google_secret_manager_secret_iam_member.
  secret_iam = merge([
    for secret, s in var.secrets : {
      for binding in s.iam : "${secret}|${binding.role}|${binding.member}" => merge(binding, { secret = secret })
    }
  ]...)
}

resource "google_secret_manager_secret" "secret" {
  for_each = var.secrets

  project   = var.project_id
  secret_id = each.key
  labels    = merge(local.labels, each.value.labels)

  # Stored in the cluster's region only.
  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
}

resource "google_secret_manager_secret_iam_member" "member" {
  for_each = local.secret_iam

  project   = var.project_id
  secret_id = google_secret_manager_secret.secret[each.value.secret].secret_id
  role      = each.value.role
  member    = each.value.member
}
