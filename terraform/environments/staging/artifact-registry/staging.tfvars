# --- GCP provider ---
# project_id is not stored in the repo: set via TF_VAR_project_id from ../.env (Makefile).
region = "asia-southeast2" # Jakarta
zone   = "asia-southeast2-a"
env    = "staging"

# --- Repositories ---
# Keys without env prefix; main.tf adds "<env>-" → repository_id "staging-shop".
# Image: asia-southeast2-docker.pkg.dev/<project>/staging-shop/<service>:<tag>
repositories = {
  "shop" = {
    format         = "DOCKER"
    description    = "Images of the 6 Go microservices (shop)"
    immutable_tags = true # tag = git SHA; never overwritten → deterministic GitOps deploys
    # Scanning disabled to save cost in the test environment.
    vulnerability_scanning = "DISABLED"

    # keep-recent wins over delete-old → the 10 newest versions per image are kept.
    cleanup_policies = {
      "delete-old" = {
        action    = "DELETE"
        condition = { tag_state = "ANY", older_than = "604800s" } # > 7 days
      }
      "keep-recent" = {
        action               = "KEEP"
        most_recent_versions = { keep_count = 10 }
      }
    }
  }
}
