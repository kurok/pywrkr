variable "name_prefix" {
  description = "Resource name prefix"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID for flow logs"
  type        = string
}

variable "enable_flow_logs" {
  description = "Enable VPC Flow Logs"
  type        = bool
  default     = false
}

variable "flow_log_retention_days" {
  description = "Flow log CloudWatch log group retention in days"
  type        = number
  default     = 7
}

variable "tags" {
  description = "Additional resource tags"
  type        = map(string)
  default     = {}
}
