output "cluster_name" {
  value = module.ecs_cluster.cluster_name
}

output "cluster_arn" {
  value = module.ecs_cluster.cluster_arn
}

output "master_service_name" {
  value = module.master.service_name
}

output "master_dns_name" {
  value = module.master.dns_name
}

output "worker_service_names" {
  value = module.workers.service_names
}

output "master_log_group_name" {
  value = module.logging.master_log_group_name
}

output "worker_log_group_name" {
  value = module.logging.worker_log_group_name
}

output "nat_eips" {
  value = module.network.nat_eips
}

output "namespace_name" {
  value = module.ecs_cluster.namespace_name
}

output "vpc_id" {
  value = module.network.vpc_id
}

output "worker_secret_parameter_name" {
  description = "SSM SecureString the operator creates for this region's master/worker secret"
  value       = data.aws_ssm_parameter.worker_secret.name
}
