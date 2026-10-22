# --- GCP provider ---
# project_id is not stored in the repo: set via TF_VAR_project_id from ../.env (Makefile).
region = "asia-southeast2" # Jakarta
zone   = "asia-southeast2-a"
env    = "staging"

# --- Secrets ---
# Keys without prefix; main.tf names them "<env>-<key>". Values are added out of band, never here.
secrets = {
  # Slack incoming webhook URL for Alertmanager. External Secrets (controller ServiceAccount
  # external-secrets/external-secrets) reads it through the SecretStore "gcp-secret-manager" in the
  # monitoring namespace. Add or rotate the value (it is read from stdin, not from the command line):
  #   gcloud secrets versions add staging-alertmanager-slack-webhook --data-file=-
  "alertmanager-slack-webhook" = {
    workload_identity = [
      { namespace = "external-secrets", service_account = "external-secrets", role = "roles/secretmanager.secretAccessor" },
    ]
  }
}
