locals {
  labels = {
    env        = var.env
    managed_by = "terraform"
  }

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
