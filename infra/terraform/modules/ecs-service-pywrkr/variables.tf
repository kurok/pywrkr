variable "name_prefix" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "cluster_id" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "assign_public_ip" {
  type    = bool
  default = true
}

variable "cloudmap_namespace" {
  type = string
}

variable "execution_role_arn" {
  type = string
}

variable "task_role_arn" {
  type = string
}

variable "container_image" {
  description = "Full container image URL (e.g. ghcr.io/kurok/pywrkr:latest)"
  type        = string
}

variable "master_cpu" {
  type    = number
  default = 1024
}

variable "master_memory" {
  type    = number
  default = 2048
}

variable "worker_cpu" {
  type    = number
  default = 1024
}

variable "worker_memory" {
  type    = number
  default = 2048
}

variable "worker_count" {
  type    = number
  default = 3
}

variable "master_log_group" {
  type = string
}

variable "worker_log_group" {
  type = string
}

variable "target_url" {
  type = string
}

variable "test_duration" {
  type    = number
  default = 300
}

variable "connections" {
  type    = number
  default = 100
}

variable "users" {
  type    = number
  default = 0
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

variable "tags" {
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

variable "worker_secret" {
  description = <<-EOT
    Shared HMAC secret for the master/worker control channel, passed to every
    container as PYWRKR_WORKER_SECRET. A master bound to 0.0.0.0 refuses to
    start without one (#303). Leave null to generate a random secret for this
    deployment; it is stored in state and in the task definitions.
  EOT
  type        = string
  default     = null
  sensitive   = true

  validation {
    condition     = var.worker_secret == null || try(length(var.worker_secret) >= 16, false)
    error_message = "worker_secret must be at least 16 characters (or null to generate one)."
  }
}
