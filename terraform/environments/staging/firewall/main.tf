data "google_compute_network" "vpc" {
  name = "${var.env}-${var.network_vpc_name}"
}

module "firewall" {
  source           = "../../../modules/firewall"
  vpc_id           = data.google_compute_network.vpc.id
  network_vpc_name = "${var.env}-${var.network_vpc_name}"
  firewall_rules   = var.firewall_rules
}
