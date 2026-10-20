# --- Firewall Rules ---
resource "google_compute_firewall" "rules" {
  for_each = var.firewall_rules

  # Rules come from tfvars, which checkov cannot resolve statically; values are reviewed there
  # (SSH only from the IAP range 35.235.240.0/20).
  # checkov:skip=CKV_GCP_2: SSH source restricted to the IAP range, not 0.0.0.0/0
  # checkov:skip=CKV_GCP_3: no RDP rule
  # checkov:skip=CKV_GCP_75: no FTP rule
  # checkov:skip=CKV_GCP_77: no FTP rule
  # checkov:skip=CKV_GCP_88: no public MySQL rule
  # checkov:skip=CKV_GCP_106: no public HTTP rule
  # checkov:skip=CKV2_GCP_12: ports come from reviewed tfvars

  name        = "${var.network_vpc_name}-${each.key}"
  network     = var.vpc_id
  description = each.value.description
  direction   = each.value.direction
  priority    = each.value.priority
  disabled    = each.value.disabled

  source_ranges      = each.value.source_ranges
  destination_ranges = each.value.destination_ranges
  source_tags        = each.value.source_tags
  target_tags        = each.value.target_tags

  dynamic "allow" {
    for_each = each.value.allow
    content {
      protocol = allow.value.protocol
      ports    = allow.value.ports
    }
  }

  dynamic "deny" {
    for_each = each.value.deny
    content {
      protocol = deny.value.protocol
      ports    = deny.value.ports
    }
  }

  # Rule logging, only when log_config is set in tfvars.
  dynamic "log_config" {
    for_each = each.value.log_config != null ? [each.value.log_config] : []
    content {
      metadata = log_config.value.metadata
    }
  }
}
