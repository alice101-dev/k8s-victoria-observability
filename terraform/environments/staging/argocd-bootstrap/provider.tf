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

data "google_container_cluster" "gke" {
  name     = "${var.env}-${var.cluster_name}"
  location = var.cluster_location
}

data "google_client_config" "default" {}

provider "helm" {
  kubernetes = {
    host  = "https://${data.google_container_cluster.gke.control_plane_endpoints_config[0].dns_endpoint_config[0].endpoint}"
    token = data.google_client_config.default.access_token
  }
}
