variable "name_prefix" {
  description = "Resource name prefix"
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
}

variable "az_count" {
  description = "Number of availability zones to use"
  type        = number
  default     = 2
}

variable "worker_subnet_count" {
  description = "Number of private worker subnets (one per desired source IP in NAT mode)"
  type        = number
  default     = 3
}

variable "egress_mode" {
  description = "'nat_eip' for NAT gateway with Elastic IPs, 'public_ip' for direct public IPs"
  type        = string
  default     = "nat_eip"
}

variable "coordination_port" {
  description = "TCP port for pywrkr master/worker coordination"
  type        = number
  default     = 9220
}

variable "tags" {
  description = "Additional resource tags"
  type        = map(string)
  default     = {}
}
