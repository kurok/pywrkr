variable "name_prefix" {
  description = "Resource name prefix"
  type        = string
}

variable "cluster_id" {
  description = "ECS cluster ID"
  type        = string
}

variable "image" {
  description = "Docker image URI including tag"
  type        = string
}

variable "cpu" {
  description = "Task CPU units"
  type        = number
  default     = 1024
}

variable "memory" {
  description = "Task memory in MB"
  type        = number
  default     = 2048
}

variable "execution_role_arn" {
  description = "ECS task execution role ARN"
  type        = string
}

variable "task_role_arn" {
  description = "ECS task role ARN"
  type        = string
}

variable "worker_count" {
  description = "Number of worker services to create"
  type        = number
  default     = 3
}

variable "worker_subnet_ids" {
  description = "List of subnet IDs, one per worker service (for source IP pinning)"
  type        = list(string)
}

variable "security_group_id" {
  description = "Worker security group ID"
  type        = string
}

variable "assign_public_ip" {
  description = "Assign public IP (true in public_ip mode)"
  type        = bool
  default     = false
}

variable "log_group_name" {
  description = "CloudWatch log group name (shared by all worker services)"
  type        = string
}

variable "aws_region" {
  description = "AWS region for log configuration"
  type        = string
}

variable "master_dns" {
  description = "Master DNS name (e.g., pywrkr-master.pywrkr.local)"
  type        = string
}

variable "coordination_port" {
  description = "TCP port for master/worker coordination"
  type        = number
  default     = 9220
}

variable "tags" {
  description = "Additional resource tags"
  type        = map(string)
  default     = {}
}
