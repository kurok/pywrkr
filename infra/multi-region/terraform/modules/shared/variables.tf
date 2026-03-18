variable "name_prefix" {
  description = "Resource name prefix ({project}-{environment})"
  type        = string
}

variable "ecr_repo_name" {
  description = "ECR repository name"
  type        = string
}

variable "replication_regions" {
  description = "List of AWS regions to replicate ECR images to"
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Additional resource tags"
  type        = map(string)
  default     = {}
}
