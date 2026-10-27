region = "asia-southeast2"
zone   = "asia-southeast2-a"
env    = "staging"

repositories = {
  "deus" = {
    format                 = "DOCKER"
    description            = "Images of the deus services"
    immutable_tags         = true
    vulnerability_scanning = "DISABLED"

    cleanup_policies = {
      "delete-old" = {
        action    = "DELETE"
        condition = { tag_state = "ANY", older_than = "604800s" }
      }
      "keep-recent" = {
        action               = "KEEP"
        most_recent_versions = { keep_count = 10 }
      }
    }
  }
}
