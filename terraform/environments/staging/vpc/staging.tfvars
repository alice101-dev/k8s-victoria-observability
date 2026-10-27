region = "asia-southeast2"
zone   = "asia-southeast2-a"
env    = "staging"

network_vpc_name = "vpc-observability"
subnets = {
  gke = {
    ip_cidr_range = "10.120.0.0/20"
    region        = "asia-southeast2"
  }
}
