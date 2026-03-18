output "flow_log_id" {
  description = "VPC Flow Log ID (empty if disabled)"
  value       = try(aws_flow_log.vpc[0].id, "")
}

output "flow_log_group_name" {
  description = "Flow Log CloudWatch log group name (empty if disabled)"
  value       = try(aws_cloudwatch_log_group.flow_logs[0].name, "")
}
