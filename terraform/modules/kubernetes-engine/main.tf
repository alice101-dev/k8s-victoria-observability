# Dedicated least-privilege node service account (not the default compute SA).
resource "google_service_account" "gke_sa" {
  account_id   = var.sa_account_id
  display_name = var.sa_display_name != "" ? var.sa_display_name : "GKE node SA ${var.cluster_name}"
}

resource "google_project_iam_member" "gke_sa_roles" {
  for_each = toset(var.sa_roles)
  project  = var.project_id
  role     = each.key
  member   = "serviceAccount:${google_service_account.gke_sa.email}"
}

# GKE Standard: private nodes, Dataplane V2, Workload Identity.
resource "google_container_cluster" "primary" {
  # checkov:skip=CKV_GCP_12: network policy enforced by Dataplane V2 (ADVANCED_DATAPATH)
  # checkov:skip=CKV_GCP_18: control plane reached via IAM-gated DNS endpoint
  # checkov:skip=CKV_GCP_65: Google Groups RBAC not used
  # checkov:skip=CKV_GCP_66: Binary Authorization not adopted
  # checkov:skip=CKV_GCP_123: "no node pools" conflicts with the GKE Standard design
  name = var.cluster_name
  # Zone (zonal cluster, control plane covered by the free tier) or region (regional).
  location = var.location

  network    = var.vpc_id
  subnetwork = var.subnet_id

  networking_mode       = "VPC_NATIVE"
  enable_shielded_nodes = true
  enable_legacy_abac    = false

  # Only system components go to Cloud Logging/Monitoring; workload telemetry goes to the
  # Victoria stack (no double ingest cost). logging_service/monitoring_service are not set:
  # the API rejects them together with logging_config/monitoring_config (Error 400).
  logging_config {
    enable_components = var.logging_components
  }
  monitoring_config {
    enable_components = var.monitoring_components
    # Managed Prometheus disabled — vmagent scrapes instead.
    managed_prometheus {
      enabled = false
    }
  }

  master_auth {
    client_certificate_config {
      issue_client_certificate = false
    }
  }

  # Default pool removed; workers come from google_container_node_pool below.
  remove_default_node_pool = true
  initial_node_count       = 1

  # Dataplane V2 (eBPF/Cilium) enforces NetworkPolicy without Calico.
  datapath_provider = "ADVANCED_DATAPATH"

  release_channel {
    channel = var.release_channel
  }

  # kubectl and Terraform (helm provider) use the IAM-gated DNS endpoint: no bastion/VPN,
  # while the IP endpoint can stay restricted.
  control_plane_endpoints_config {
    dns_endpoint_config {
      allow_external_traffic = true
    }
  }

  dynamic "master_authorized_networks_config" {
    for_each = length(var.master_authorized_networks) > 0 ? [1] : []
    content {
      dynamic "cidr_blocks" {
        for_each = var.master_authorized_networks
        content {
          cidr_block   = cidr_blocks.value.cidr_block
          display_name = cidr_blocks.value.display_name
        }
      }
    }
  }

  private_cluster_config {
    enable_private_nodes   = true
    master_ipv4_cidr_block = var.master_ipv4_cidr_block
  }

  security_posture_config {
    mode               = "BASIC"
    vulnerability_mode = "VULNERABILITY_DISABLED"
  }

  deletion_protection = false

  resource_labels = {
    env        = var.env
    managed_by = "terraform"
  }

  node_pool_defaults {
    node_config_defaults {
      gcfs_config {
        enabled = true
      }
    }
  }

  addons_config {
    dns_cache_config {
      enabled = true
    }
    gce_persistent_disk_csi_driver_config {
      enabled = true
    }
    http_load_balancing {
      disabled = false
    }
  }

  # start_time must be UTC ("Z"); GKE normalises to UTC (avoids a permanent diff).
  maintenance_policy {
    recurring_window {
      start_time = var.maintenance_start_time
      end_time   = timeadd(var.maintenance_start_time, "4h")
      recurrence = var.maintenance_recurrence
    }
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  ip_allocation_policy {
    cluster_ipv4_cidr_block  = var.cluster_ipv4_cidr_block
    services_ipv4_cidr_block = var.services_ipv4_cidr_block
  }

  dns_config {
    cluster_dns = "KUBE_DNS"
  }

  # Only for the temporary initial node (removed right away), still hardened.
  node_config {
    service_account = google_service_account.gke_sa.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }
    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  lifecycle {
    ignore_changes = [
      # Node version is managed by the release channel auto-upgrades.
      node_version,
      # Labels may be added out of band by GKE/tooling.
      resource_labels,
      # Computed by GKE after creation; pod/service CIDRs are immutable.
      ip_allocation_policy,
      # Only used for the removed initial node; GKE copies pool settings here,
      # which would otherwise force a cluster replacement.
      node_config,
    ]
  }
}

# Node pools: Spot VMs only (cheap, can be preempted at any time).
resource "google_container_node_pool" "pools" {
  for_each = var.node_pools

  name     = "${var.env}-${each.key}"
  cluster  = google_container_cluster.primary.id
  location = var.location

  # Zonal cluster: nodes land in the cluster zone; node_locations only when set.
  node_locations = each.value.node_locations

  initial_node_count = each.value.min_node_count
  autoscaling {
    min_node_count = each.value.min_node_count
    max_node_count = each.value.max_node_count
  }

  node_config {
    machine_type = each.value.machine_type
    image_type   = "COS_CONTAINERD"
    disk_size_gb = each.value.disk_size_gb
    disk_type    = each.value.disk_type
    spot         = each.value.spot

    service_account = google_service_account.gke_sa.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    resource_labels = {
      env        = var.env
      managed_by = "terraform"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    gcfs_config {
      enabled = true
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    labels = merge({
      env       = var.env
      node-pool = each.key
    }, each.value.labels)

    tags = each.value.tags
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  upgrade_settings {
    strategy        = "SURGE"
    max_surge       = 1
    max_unavailable = 0
  }

  lifecycle {
    ignore_changes = [
      # The autoscaler changes the actual node count.
      initial_node_count,
      # GKE auto-upgrade bumps the node version.
      version,
      node_config[0].labels,
      node_config[0].resource_labels,
    ]
  }
}
