module "vpc" {
  source           = "../../../modules/vpc"
  network_vpc_name = "${var.env}-${var.network_vpc_name}"
  subnets          = var.subnets
}
