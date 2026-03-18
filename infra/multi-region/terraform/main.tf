# -----------------------------------------------------------------------------
# Root module — shared resources + static per-region module blocks
#
# Terraform does not support dynamic provider aliases in for_each, so each
# supported region gets a static module block gated by count.
# To add a new region: add a provider alias in providers.tf and a module block here.
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Shared layer — ECR repository + cross-region replication
# -----------------------------------------------------------------------------

module "shared" {
  source = "./modules/shared"

  name_prefix         = local.name_prefix
  ecr_repo_name       = "${local.name_prefix}-pywrkr"
  replication_regions = local.ecr_replication_regions
  tags                = local.common_tags
}

# -----------------------------------------------------------------------------
# Regional test cells — one module block per supported region
# Enable/disable via var.regions["<region>"].enabled
# -----------------------------------------------------------------------------

locals {
  # ECR image URI — each region has its own ECR endpoint after replication
  ecr_image_base = module.shared.ecr_repository_url
  ecr_image      = "${local.ecr_image_base}:${var.image_tag}"

  # Per-region pywrkr tags include the region name
  region_pywrkr_tags = {
    for region in keys(local.enabled_regions) : region => merge(var.tags, {
      region      = region
      environment = var.environment
    })
  }
}

# --- us-east-1 ---

module "region_us_east_1" {
  count  = try(var.regions["us-east-1"].enabled, false) ? 1 : 0
  source = "./modules/regional"
  providers = {
    aws = aws.us_east_1
  }

  name_prefix             = "${local.name_prefix}-use1"
  region_name             = "us-east-1"
  vpc_cidr                = var.regions["us-east-1"].vpc_cidr
  az_count                = var.regions["us-east-1"].az_count
  worker_count            = var.regions["us-east-1"].worker_count
  egress_mode             = var.egress_mode
  enable_flow_logs        = var.enable_flow_logs
  flow_log_retention_days = var.flow_log_retention_days
  cloudmap_namespace      = var.cloudmap_namespace
  image                   = local.ecr_image
  master_cpu              = var.regions["us-east-1"].master_cpu
  master_memory           = var.regions["us-east-1"].master_memory
  worker_cpu              = var.regions["us-east-1"].worker_cpu
  worker_memory           = var.regions["us-east-1"].worker_memory
  target_url              = var.target_url
  test_duration           = var.test_duration
  users                   = var.users
  connections             = var.connections
  rate                    = var.rate
  thresholds              = var.thresholds
  scenario_file           = var.scenario_file
  pywrkr_tags             = local.region_pywrkr_tags["us-east-1"]
  otel_endpoint           = var.otel_endpoint
  prom_remote_write       = var.prom_remote_write
  log_retention_days      = var.log_retention_days
  tags                    = local.common_tags
}

# --- eu-west-1 ---

module "region_eu_west_1" {
  count  = try(var.regions["eu-west-1"].enabled, false) ? 1 : 0
  source = "./modules/regional"
  providers = {
    aws = aws.eu_west_1
  }

  name_prefix             = "${local.name_prefix}-euw1"
  region_name             = "eu-west-1"
  vpc_cidr                = var.regions["eu-west-1"].vpc_cidr
  az_count                = var.regions["eu-west-1"].az_count
  worker_count            = var.regions["eu-west-1"].worker_count
  egress_mode             = var.egress_mode
  enable_flow_logs        = var.enable_flow_logs
  flow_log_retention_days = var.flow_log_retention_days
  cloudmap_namespace      = var.cloudmap_namespace
  image                   = local.ecr_image
  master_cpu              = var.regions["eu-west-1"].master_cpu
  master_memory           = var.regions["eu-west-1"].master_memory
  worker_cpu              = var.regions["eu-west-1"].worker_cpu
  worker_memory           = var.regions["eu-west-1"].worker_memory
  target_url              = var.target_url
  test_duration           = var.test_duration
  users                   = var.users
  connections             = var.connections
  rate                    = var.rate
  thresholds              = var.thresholds
  scenario_file           = var.scenario_file
  pywrkr_tags             = local.region_pywrkr_tags["eu-west-1"]
  otel_endpoint           = var.otel_endpoint
  prom_remote_write       = var.prom_remote_write
  log_retention_days      = var.log_retention_days
  tags                    = local.common_tags
}

# --- ap-southeast-1 ---

module "region_ap_southeast_1" {
  count  = try(var.regions["ap-southeast-1"].enabled, false) ? 1 : 0
  source = "./modules/regional"
  providers = {
    aws = aws.ap_southeast_1
  }

  name_prefix             = "${local.name_prefix}-apse1"
  region_name             = "ap-southeast-1"
  vpc_cidr                = var.regions["ap-southeast-1"].vpc_cidr
  az_count                = var.regions["ap-southeast-1"].az_count
  worker_count            = var.regions["ap-southeast-1"].worker_count
  egress_mode             = var.egress_mode
  enable_flow_logs        = var.enable_flow_logs
  flow_log_retention_days = var.flow_log_retention_days
  cloudmap_namespace      = var.cloudmap_namespace
  image                   = local.ecr_image
  master_cpu              = var.regions["ap-southeast-1"].master_cpu
  master_memory           = var.regions["ap-southeast-1"].master_memory
  worker_cpu              = var.regions["ap-southeast-1"].worker_cpu
  worker_memory           = var.regions["ap-southeast-1"].worker_memory
  target_url              = var.target_url
  test_duration           = var.test_duration
  users                   = var.users
  connections             = var.connections
  rate                    = var.rate
  thresholds              = var.thresholds
  scenario_file           = var.scenario_file
  pywrkr_tags             = local.region_pywrkr_tags["ap-southeast-1"]
  otel_endpoint           = var.otel_endpoint
  prom_remote_write       = var.prom_remote_write
  log_retention_days      = var.log_retention_days
  tags                    = local.common_tags
}
