region = "asia-southeast2"
zone   = "asia-southeast2-a"
env    = "staging"

cloud_nat_configs = {
  "vpc-observability-main" = {
    vpc_name = "vpc-observability"
    region   = "asia-southeast2"

    nat_ip_allocate_option = "AUTO_ONLY"

    enable_dynamic_port_allocation = true
    min_ports_per_vm               = 64
    max_ports_per_vm               = 4096

    enable_logging = true
    log_filter     = "ERRORS_ONLY"
  }
}
