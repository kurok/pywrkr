# -----------------------------------------------------------------------------
# Outputs — aggregated across all enabled regions
# -----------------------------------------------------------------------------

output "ecr_repository_url" {
  description = "ECR repository URL in the home region"
  value       = module.shared.ecr_repository_url
}

# --- Per-region outputs ---
# Each output is a map of region → value, only including enabled regions.

output "cluster_names" {
  description = "Map of region to ECS cluster name"
  value = merge(
    try(var.regions["us-east-1"].enabled, false) ? { "us-east-1" = module.region_us_east_1[0].cluster_name } : {},
    try(var.regions["eu-west-1"].enabled, false) ? { "eu-west-1" = module.region_eu_west_1[0].cluster_name } : {},
    try(var.regions["ap-southeast-1"].enabled, false) ? { "ap-southeast-1" = module.region_ap_southeast_1[0].cluster_name } : {},
  )
}

output "master_service_names" {
  description = "Map of region to master ECS service name"
  value = merge(
    try(var.regions["us-east-1"].enabled, false) ? { "us-east-1" = module.region_us_east_1[0].master_service_name } : {},
    try(var.regions["eu-west-1"].enabled, false) ? { "eu-west-1" = module.region_eu_west_1[0].master_service_name } : {},
    try(var.regions["ap-southeast-1"].enabled, false) ? { "ap-southeast-1" = module.region_ap_southeast_1[0].master_service_name } : {},
  )
}

output "master_dns_names" {
  description = "Map of region to master Cloud Map DNS name"
  value = merge(
    try(var.regions["us-east-1"].enabled, false) ? { "us-east-1" = module.region_us_east_1[0].master_dns_name } : {},
    try(var.regions["eu-west-1"].enabled, false) ? { "eu-west-1" = module.region_eu_west_1[0].master_dns_name } : {},
    try(var.regions["ap-southeast-1"].enabled, false) ? { "ap-southeast-1" = module.region_ap_southeast_1[0].master_dns_name } : {},
  )
}

output "worker_service_names" {
  description = "Map of region to list of worker ECS service names"
  value = merge(
    try(var.regions["us-east-1"].enabled, false) ? { "us-east-1" = module.region_us_east_1[0].worker_service_names } : {},
    try(var.regions["eu-west-1"].enabled, false) ? { "eu-west-1" = module.region_eu_west_1[0].worker_service_names } : {},
    try(var.regions["ap-southeast-1"].enabled, false) ? { "ap-southeast-1" = module.region_ap_southeast_1[0].worker_service_names } : {},
  )
}

output "master_log_group_names" {
  description = "Map of region to master CloudWatch log group name"
  value = merge(
    try(var.regions["us-east-1"].enabled, false) ? { "us-east-1" = module.region_us_east_1[0].master_log_group_name } : {},
    try(var.regions["eu-west-1"].enabled, false) ? { "eu-west-1" = module.region_eu_west_1[0].master_log_group_name } : {},
    try(var.regions["ap-southeast-1"].enabled, false) ? { "ap-southeast-1" = module.region_ap_southeast_1[0].master_log_group_name } : {},
  )
}

output "worker_log_group_names" {
  description = "Map of region to worker CloudWatch log group name"
  value = merge(
    try(var.regions["us-east-1"].enabled, false) ? { "us-east-1" = module.region_us_east_1[0].worker_log_group_name } : {},
    try(var.regions["eu-west-1"].enabled, false) ? { "eu-west-1" = module.region_eu_west_1[0].worker_log_group_name } : {},
    try(var.regions["ap-southeast-1"].enabled, false) ? { "ap-southeast-1" = module.region_ap_southeast_1[0].worker_log_group_name } : {},
  )
}

output "nat_eips" {
  description = "Map of region to list of NAT gateway Elastic IP addresses"
  value = merge(
    try(var.regions["us-east-1"].enabled, false) ? { "us-east-1" = module.region_us_east_1[0].nat_eips } : {},
    try(var.regions["eu-west-1"].enabled, false) ? { "eu-west-1" = module.region_eu_west_1[0].nat_eips } : {},
    try(var.regions["ap-southeast-1"].enabled, false) ? { "ap-southeast-1" = module.region_ap_southeast_1[0].nat_eips } : {},
  )
}

output "cloudmap_namespaces" {
  description = "Map of region to Cloud Map namespace name"
  value = merge(
    try(var.regions["us-east-1"].enabled, false) ? { "us-east-1" = module.region_us_east_1[0].namespace_name } : {},
    try(var.regions["eu-west-1"].enabled, false) ? { "eu-west-1" = module.region_eu_west_1[0].namespace_name } : {},
    try(var.regions["ap-southeast-1"].enabled, false) ? { "ap-southeast-1" = module.region_ap_southeast_1[0].namespace_name } : {},
  )
}
