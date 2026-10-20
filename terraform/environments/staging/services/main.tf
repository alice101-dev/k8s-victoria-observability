# --- API Activation ---
module "services" {
  source             = "../../../modules/services"
  project_id         = var.project_id
  enabled_apis       = var.enabled_apis
  disable_on_destroy = var.disable_on_destroy
}
