# -----------------------------------------------------------------------------
# Global configuration
# -----------------------------------------------------------------------------

variable "project_name" {
  description = "Project name used in resource naming"
  type        = string
  default     = "pywrkr"
}

variable "environment" {
  description = "Environment name (e.g., loadtest, staging, prod)"
  type        = string
  default     = "loadtest"
}

variable "home_region" {
  description = "Home AWS region for ECR repository and default provider"
  type        = string
  default     = "us-east-1"
}

# -----------------------------------------------------------------------------
# Regional configuration
# -----------------------------------------------------------------------------

variable "regions" {
  description = "Map of AWS region name to region-specific configuration"
  type = map(object({
    enabled       = bool
    vpc_cidr      = string
    az_count      = optional(number, 2)
    worker_count  = optional(number, 3)
    master_cpu    = optional(number, 1024)
    master_memory = optional(number, 2048)
    worker_cpu    = optional(number, 1024)
    worker_memory = optional(number, 2048)
  }))

  validation {
    condition = alltrue([
      for region, config in var.regions :
      config.worker_count >= 1 && config.worker_count <= var.max_workers_per_region
    ])
    error_message = "worker_count must be between 1 and max_workers_per_region for each region."
  }

  validation {
    condition = alltrue([
      for region, config in var.regions :
      config.worker_count <= config.az_count * 2 if config.enabled
    ])
    error_message = "worker_count must not exceed 2 * az_count per region (max 2 workers per AZ)."
  }

  validation {
    condition = length(distinct([
      for region, config in var.regions : config.vpc_cidr if config.enabled
      ])) == length([
      for region, config in var.regions : config.vpc_cidr if config.enabled
    ])
    error_message = "VPC CIDRs must be unique across enabled regions to avoid conflicts."
  }
}

# -----------------------------------------------------------------------------
# Networking
# -----------------------------------------------------------------------------

variable "egress_mode" {
  description = "Egress strategy: 'nat_eip' for stable source IPs via NAT/EIP, 'public_ip' for ephemeral public IPs"
  type        = string
  default     = "nat_eip"

  validation {
    condition     = contains(["nat_eip", "public_ip"], var.egress_mode)
    error_message = "egress_mode must be 'nat_eip' or 'public_ip'."
  }
}

variable "enable_flow_logs" {
  description = "Enable VPC Flow Logs to CloudWatch"
  type        = bool
  default     = false
}

variable "flow_log_retention_days" {
  description = "Retention period for VPC Flow Log group in days"
  type        = number
  default     = 7
}

# -----------------------------------------------------------------------------
# Test configuration
# -----------------------------------------------------------------------------

variable "target_url" {
  description = "URL to benchmark (e.g., https://api.example.com)"
  type        = string
}

variable "test_duration" {
  description = "Test duration in seconds, passed to pywrkr -d flag as a float"
  type        = number
  default     = 60
}

variable "users" {
  description = "Number of virtual users per worker (0 = connection-based mode)"
  type        = number
  default     = 0
}

variable "connections" {
  description = "Number of concurrent connections per worker"
  type        = number
  default     = 10
}

variable "rate" {
  description = "Request rate limit per worker in req/s (0 = unlimited)"
  type        = number
  default     = 0
}

variable "thresholds" {
  description = "List of threshold expressions (e.g., ['p95 < 500ms', 'error_rate < 5%'])"
  type        = list(string)
  default     = []
}

variable "scenario_file" {
  description = "Path to scenario JSON file inside the container (e.g., '/scenarios/api-flow.json')"
  type        = string
  default     = ""
}

variable "tags" {
  description = "Additional tags to pass to pywrkr via --tag key=value"
  type        = map(string)
  default     = {}
}

# -----------------------------------------------------------------------------
# Container image
# -----------------------------------------------------------------------------

variable "image_tag" {
  description = "Docker image tag to deploy"
  type        = string
  default     = "latest"
}

# -----------------------------------------------------------------------------
# Observability
# -----------------------------------------------------------------------------

variable "otel_endpoint" {
  description = "OpenTelemetry collector HTTP endpoint (e.g., https://otel.example.com:4318)"
  type        = string
  default     = ""
}

variable "prom_remote_write" {
  description = "Prometheus Pushgateway URL for metrics export"
  type        = string
  default     = ""
}

variable "log_retention_days" {
  description = "CloudWatch Log group retention in days"
  type        = number
  default     = 7
}

# -----------------------------------------------------------------------------
# Safety controls
# -----------------------------------------------------------------------------

variable "confirm_production" {
  description = "Must be 'yes' to target non-internal URLs. Safety gate to prevent accidental production load."
  type        = string
  default     = "no"
}

variable "max_duration_seconds" {
  description = "Maximum allowed test duration in seconds. Safety cap."
  type        = number
  default     = 300
}

variable "max_workers_per_region" {
  description = "Maximum allowed workers per region. Safety cap."
  type        = number
  default     = 10
}

# -----------------------------------------------------------------------------
# Cloud Map
# -----------------------------------------------------------------------------

variable "cloudmap_namespace" {
  description = "Cloud Map private DNS namespace name"
  type        = string
  default     = "pywrkr.local"
}
