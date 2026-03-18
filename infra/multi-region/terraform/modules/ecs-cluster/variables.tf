variable "name_prefix" {
  description = "Resource name prefix"
  type        = string
}

variable "cloudmap_namespace" {
  description = "Cloud Map private DNS namespace name"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID for Cloud Map namespace"
  type        = string
}

variable "tags" {
  description = "Additional resource tags"
  type        = map(string)
  default     = {}
}
