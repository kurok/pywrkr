output "master_log_group_name" {
  description = "Master CloudWatch log group name"
  value       = aws_cloudwatch_log_group.master.name
}

output "master_log_group_arn" {
  description = "Master CloudWatch log group ARN"
  value       = aws_cloudwatch_log_group.master.arn
}

output "worker_log_group_name" {
  description = "Worker CloudWatch log group name (shared by all worker services)"
  value       = aws_cloudwatch_log_group.worker.name
}

output "worker_log_group_arn" {
  description = "Worker CloudWatch log group ARN"
  value       = aws_cloudwatch_log_group.worker.arn
}
