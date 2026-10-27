region = "asia-southeast2"
zone   = "asia-southeast2-a"
env    = "staging"

secrets = {
  "alertmanager-slack-webhook" = {
    workload_identity = [
      { namespace = "external-secrets", service_account = "external-secrets", role = "roles/secretmanager.secretAccessor" },
    ]
  }
}
