output "service_name" {
  description = "Master ECS service name"
  value       = aws_ecs_service.master.name
}

output "service_arn" {
  description = "Master ECS service ARN"
  value       = aws_ecs_service.master.id
}

output "task_definition_arn" {
  description = "Master task definition ARN"
  value       = aws_ecs_task_definition.master.arn
}

output "dns_name" {
  description = "Master Cloud Map DNS name"
  value       = "pywrkr-master.${var.cloudmap_namespace}"
}

output "discovery_service_arn" {
  description = "Cloud Map service ARN"
  value       = aws_service_discovery_service.master.arn
}
