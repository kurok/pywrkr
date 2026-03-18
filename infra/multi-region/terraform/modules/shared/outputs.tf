output "ecr_repository_url" {
  description = "ECR repository URL in the home region"
  value       = aws_ecr_repository.pywrkr.repository_url
}

output "ecr_repository_arn" {
  description = "ECR repository ARN"
  value       = aws_ecr_repository.pywrkr.arn
}

output "ecr_registry_id" {
  description = "ECR registry ID (AWS account ID)"
  value       = aws_ecr_repository.pywrkr.registry_id
}
