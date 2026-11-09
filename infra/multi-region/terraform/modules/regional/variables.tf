variable "name_prefix" {
  description = "Resource name prefix"
  type        = string
}

variable "region_name" {
  description = "AWS region name (e.g., us-east-1)"
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR for this region"
  type        = string
}

variable "az_count" {
  description = "Number of AZs"
  type        = number
  default     = 2
}

variable "worker_count" {
  description = "Number of worker services"
  type        = number
  default     = 3
}

variable "egress_mode" {
  description = "Egress mode: nat_eip or public_ip"
  type        = string
}

variable "enable_flow_logs" {
  description = "Enable VPC Flow Logs"
  type        = bool
  default     = false
}

variable "flow_log_retention_days" {
  description = "Flow log retention in days"
  type        = number
  default     = 7
}

variable "cloudmap_namespace" {
  description = "Cloud Map namespace name"
  type        = string
}

variable "image" {
  description = "Docker image URI"
  type        = string
}

variable "master_cpu" {
  description = "Master CPU units"
  type        = number
  default     = 1024
}

variable "master_memory" {
  description = "Master memory in MB"
  type        = number
  default     = 2048
}

variable "worker_cpu" {
  description = "Worker CPU units"
  type        = number
  default     = 1024
}

variable "worker_memory" {
  description = "Worker memory in MB"
  type        = number
  default     = 2048
}

variable "target_url" {
  type = string
}

variable "test_duration" {
  type = number
}

variable "users" {
  type    = number
  default = 0
}

variable "connections" {
  type    = number
  default = 10
}

variable "rate" {
  type    = number
  default = 0
}

variable "thresholds" {
  type    = list(string)
  default = []
}

variable "scenario_file" {
  type    = string
  default = ""
}

variable "pywrkr_tags" {
  type    = map(string)
  default = {}
}

variable "otel_endpoint" {
  type    = string
  default = ""
}

variable "prom_remote_write" {
  type    = string
  default = ""
}

variable "log_retention_days" {
  type    = number
  default = 7
}

variable "tags" {
  type    = map(string)
  default = {}
}
