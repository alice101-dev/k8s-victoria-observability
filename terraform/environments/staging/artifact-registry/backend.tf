terraform {
  # Local state (terraform.tfstate in this folder), git-ignored: never commit it.
  backend "local" {
    path = "terraform.tfstate"
  }
}
