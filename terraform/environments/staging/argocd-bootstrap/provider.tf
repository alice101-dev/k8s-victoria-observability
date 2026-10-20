# Pinned Terraform and provider versions.
terraform {
  required_version = "~> 1.15.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.35.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.1.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

# Cluster looked up by name (no terraform_remote_state).
data "google_container_cluster" "gke" {
  name     = "${var.env}-${var.cluster_name}"
  location = var.cluster_location
}

data "google_client_config" "default" {}

# API server via the GKE DNS endpoint (public Google certificate, gcloud/ADC token):
# no bastion/VPN even with private nodes and a restricted IP endpoint.
provider "helm" {
  kubernetes = {
    host  = "https://${data.google_container_cluster.gke.control_plane_endpoints_config[0].dns_endpoint_config[0].endpoint}"
    token = data.google_client_config.default.access_token
  }
}
