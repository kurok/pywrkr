output "service_names" {
  description = "List of worker ECS service names"
  value       = aws_ecs_service.worker[*].name
}

output "service_arns" {
  description = "List of worker ECS service ARNs"
  value       = aws_ecs_service.worker[*].id
}

output "task_definition_arn" {
  description = "Worker task definition ARN (shared by all worker services)"
  value       = aws_ecs_task_definition.worker.arn
}
