variable "name_prefix" {
  description = "Resource name prefix"
  type        = string
}

variable "cluster_id" {
  description = "ECS cluster ID"
  type        = string
}

variable "namespace_id" {
  description = "Cloud Map namespace ID for service registration"
  type        = string
}

variable "image" {
  description = "Docker image URI including tag (e.g., 123456.dkr.ecr.us-east-1.amazonaws.com/pywrkr:v1)"
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

variable "subnet_id" {
  description = "Subnet ID for master task"
  type        = string
}

variable "security_group_id" {
  description = "Security group ID for master task"
  type        = string
}

variable "assign_public_ip" {
  description = "Assign public IP to master task (true in public_ip mode)"
  type        = bool
  default     = false
}

variable "log_group_name" {
  description = "CloudWatch log group name"
  type        = string
}

variable "aws_region" {
  description = "AWS region for log configuration"
  type        = string
}

variable "coordination_port" {
  description = "TCP port for master/worker coordination"
  type        = number
  default     = 9220
}

variable "cloudmap_namespace" {
  description = "Cloud Map namespace name for DNS"
  type        = string
}

# --- Test configuration ---

variable "worker_count" {
  description = "Number of workers the master should expect"
  type        = number
}

variable "target_url" {
  description = "Target URL to benchmark"
  type        = string
}

variable "test_duration" {
  description = "Test duration in seconds"
  type        = number
}

variable "users" {
  description = "Virtual users per worker (0 = connection-based mode)"
  type        = number
  default     = 0
}

variable "connections" {
  description = "Concurrent connections per worker"
  type        = number
  default     = 10
}

variable "rate" {
  description = "Request rate per worker (0 = unlimited)"
  type        = number
  default     = 0
}

variable "thresholds" {
  description = "List of threshold expressions"
  type        = list(string)
  default     = []
}

variable "scenario_file" {
  description = "Scenario file path inside container (empty = simple URL mode)"
  type        = string
  default     = ""
}

variable "pywrkr_tags" {
  description = "Tags to pass to pywrkr --tag"
  type        = map(string)
  default     = {}
}

variable "otel_endpoint" {
  description = "OTel collector endpoint"
  type        = string
  default     = ""
}

variable "prom_remote_write" {
  description = "Prometheus pushgateway URL"
  type        = string
  default     = ""
}

variable "tags" {
  description = "Additional resource tags"
  type        = map(string)
  default     = {}
}
