variable "name_prefix" {
  description = "Resource name prefix"
  type        = string
}

variable "retention_days" {
  description = "Log retention in days"
  type        = number
  default     = 7
}

variable "tags" {
  description = "Additional resource tags"
  type        = map(string)
  default     = {}
}
