locals {
  name_prefix = "${var.project_name}-${var.environment}"

  # Collect enabled regions for outputs and shared module
  enabled_regions = {
    for region, config in var.regions : region => config
    if config.enabled
  }

  # List of region names for ECR replication targets (exclude home region)
  ecr_replication_regions = [
    for region in keys(local.enabled_regions) : region
    if region != var.home_region
  ]

  # Common tags passed to modules (in addition to provider default_tags)
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}
