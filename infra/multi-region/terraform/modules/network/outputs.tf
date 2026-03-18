output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "vpc_cidr" {
  description = "VPC CIDR block"
  value       = aws_vpc.main.cidr_block
}

output "public_subnet_ids" {
  description = "List of public subnet IDs"
  value       = aws_subnet.public[*].id
}

output "master_subnet_id" {
  description = "Master private subnet ID"
  value       = aws_subnet.master.id
}

output "worker_subnet_ids" {
  description = "List of worker private subnet IDs, indexed by worker index"
  value       = aws_subnet.worker[*].id
}

output "master_sg_id" {
  description = "Master security group ID"
  value       = aws_security_group.master.id
}

output "worker_sg_id" {
  description = "Worker security group ID"
  value       = aws_security_group.worker.id
}

output "nat_eips" {
  description = "List of NAT gateway Elastic IP addresses (empty in public_ip mode)"
  value       = aws_eip.worker_nat[*].public_ip
}

output "nat_eip_allocation_ids" {
  description = "List of NAT gateway EIP allocation IDs"
  value       = aws_eip.worker_nat[*].allocation_id
}
