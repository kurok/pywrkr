# -----------------------------------------------------------------------------
# Data sources
# -----------------------------------------------------------------------------

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  # CIDR allocation strategy:
  # Given a /16 VPC (e.g., 10.1.0.0/16), split into /20 blocks:
  #   public subnets:        10.1.0.0/20, 10.1.16.0/20      (indices 0..az_count-1)
  #   master subnet:         10.1.32.0/20                     (index az_count)
  #   worker subnets:        10.1.48.0/20, 10.1.64.0/20, ... (indices az_count+1..az_count+worker_count)
  # /20 gives 4094 usable IPs per subnet — far more than needed for Fargate tasks.
  public_subnet_cidrs = [
    for i in range(var.az_count) :
    cidrsubnet(var.vpc_cidr, 4, i)
  ]

  master_subnet_cidr = cidrsubnet(var.vpc_cidr, 4, var.az_count)

  worker_subnet_cidrs = [
    for i in range(var.worker_subnet_count) :
    cidrsubnet(var.vpc_cidr, 4, var.az_count + 1 + i)
  ]
}

# -----------------------------------------------------------------------------
# VPC
# -----------------------------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-vpc"
  })
}

# -----------------------------------------------------------------------------
# Internet Gateway
# -----------------------------------------------------------------------------

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-igw"
  })
}

# -----------------------------------------------------------------------------
# Public Subnets (NAT gateway placement or public-IP Fargate tasks)
# -----------------------------------------------------------------------------

resource "aws_subnet" "public" {
  count = var.az_count

  vpc_id                  = aws_vpc.main.id
  cidr_block              = local.public_subnet_cidrs[count.index]
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = false

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-public-${local.azs[count.index]}"
    Tier = "public"
  })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-public-rt"
  })
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}

resource "aws_route_table_association" "public" {
  count = var.az_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# -----------------------------------------------------------------------------
# Master Private Subnet
# Routed through the first NAT gateway (or first public subnet's IGW in
# public_ip mode). Master doesn't generate test traffic — just needs
# outbound for CloudWatch/OTel/ECR.
# -----------------------------------------------------------------------------

resource "aws_subnet" "master" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = local.master_subnet_cidr
  availability_zone = local.azs[0]

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-master-private"
    Tier = "private"
    Role = "master"
  })
}

resource "aws_route_table" "master" {
  vpc_id = aws_vpc.main.id

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-master-rt"
  })
}

resource "aws_route" "master_nat" {
  count = var.egress_mode == "nat_eip" ? 1 : 0

  route_table_id         = aws_route_table.master.id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.worker[0].id
}

resource "aws_route" "master_igw" {
  count = var.egress_mode == "public_ip" ? 1 : 0

  route_table_id         = aws_route_table.master.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}

resource "aws_route_table_association" "master" {
  subnet_id      = aws_subnet.master.id
  route_table_id = aws_route_table.master.id
}

# -----------------------------------------------------------------------------
# Worker Private Subnets — one per desired source IP
# Each gets its own route table pointing to a dedicated NAT gateway.
# -----------------------------------------------------------------------------

resource "aws_subnet" "worker" {
  count = var.worker_subnet_count

  vpc_id            = aws_vpc.main.id
  cidr_block        = local.worker_subnet_cidrs[count.index]
  availability_zone = local.azs[count.index % length(local.azs)]

  tags = merge(var.tags, {
    Name        = "${var.name_prefix}-worker-${count.index}-private"
    Tier        = "private"
    Role        = "worker"
    WorkerIndex = tostring(count.index)
  })
}

resource "aws_route_table" "worker" {
  count = var.worker_subnet_count

  vpc_id = aws_vpc.main.id

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-worker-${count.index}-rt"
  })
}

resource "aws_route" "worker_nat" {
  count = var.egress_mode == "nat_eip" ? var.worker_subnet_count : 0

  route_table_id         = aws_route_table.worker[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.worker[count.index].id
}

resource "aws_route" "worker_igw" {
  count = var.egress_mode == "public_ip" ? var.worker_subnet_count : 0

  route_table_id         = aws_route_table.worker[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}

resource "aws_route_table_association" "worker" {
  count = var.worker_subnet_count

  subnet_id      = aws_subnet.worker[count.index].id
  route_table_id = aws_route_table.worker[count.index].id
}

# -----------------------------------------------------------------------------
# NAT Gateways + Elastic IPs (nat_eip mode only)
# One NAT gateway per worker subnet for source IP isolation.
# EIPs have no lifecycle.prevent_destroy — terraform destroy releases them.
# -----------------------------------------------------------------------------

resource "aws_eip" "worker_nat" {
  count  = var.egress_mode == "nat_eip" ? var.worker_subnet_count : 0
  domain = "vpc"

  tags = merge(var.tags, {
    Name        = "${var.name_prefix}-worker-${count.index}-eip"
    Role        = "worker-nat"
    WorkerIndex = tostring(count.index)
  })
}

resource "aws_nat_gateway" "worker" {
  count = var.egress_mode == "nat_eip" ? var.worker_subnet_count : 0

  allocation_id = aws_eip.worker_nat[count.index].id
  subnet_id     = aws_subnet.public[count.index % length(aws_subnet.public)].id

  tags = merge(var.tags, {
    Name        = "${var.name_prefix}-worker-${count.index}-nat"
    WorkerIndex = tostring(count.index)
  })

  depends_on = [aws_internet_gateway.main]
}

# -----------------------------------------------------------------------------
# Security Groups
# -----------------------------------------------------------------------------

resource "aws_security_group" "master" {
  name_prefix = "${var.name_prefix}-master-"
  description = "pywrkr master — allows inbound coordination from workers"
  vpc_id      = aws_vpc.main.id

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-master-sg"
  })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "worker" {
  name_prefix = "${var.name_prefix}-worker-"
  description = "pywrkr worker — outbound to master and target endpoints"
  vpc_id      = aws_vpc.main.id

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-worker-sg"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# --- Master SG Rules ---

resource "aws_vpc_security_group_ingress_rule" "master_from_workers" {
  security_group_id            = aws_security_group.master.id
  referenced_security_group_id = aws_security_group.worker.id
  from_port                    = var.coordination_port
  to_port                      = var.coordination_port
  ip_protocol                  = "tcp"
  description                  = "pywrkr coordination from workers"
}

resource "aws_vpc_security_group_egress_rule" "master_https" {
  security_group_id = aws_security_group.master.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  description       = "HTTPS to AWS APIs and observability endpoints"
}

resource "aws_vpc_security_group_egress_rule" "master_http" {
  security_group_id = aws_security_group.master.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
  description       = "HTTP to target endpoints"
}

resource "aws_vpc_security_group_egress_rule" "master_dns_udp" {
  security_group_id = aws_security_group.master.id
  cidr_ipv4         = var.vpc_cidr
  from_port         = 53
  to_port           = 53
  ip_protocol       = "udp"
  description       = "DNS resolution within VPC (Cloud Map)"
}

resource "aws_vpc_security_group_egress_rule" "master_dns_tcp" {
  security_group_id = aws_security_group.master.id
  cidr_ipv4         = var.vpc_cidr
  from_port         = 53
  to_port           = 53
  ip_protocol       = "tcp"
  description       = "DNS resolution within VPC (Cloud Map, TCP fallback)"
}

# --- Worker SG Rules ---

resource "aws_vpc_security_group_egress_rule" "worker_to_master" {
  security_group_id            = aws_security_group.worker.id
  referenced_security_group_id = aws_security_group.master.id
  from_port                    = var.coordination_port
  to_port                      = var.coordination_port
  ip_protocol                  = "tcp"
  description                  = "pywrkr coordination to master"
}

resource "aws_vpc_security_group_egress_rule" "worker_https" {
  security_group_id = aws_security_group.worker.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  description       = "HTTPS to target and AWS APIs"
}

resource "aws_vpc_security_group_egress_rule" "worker_http" {
  security_group_id = aws_security_group.worker.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
  description       = "HTTP to target endpoints"
}

resource "aws_vpc_security_group_egress_rule" "worker_dns_udp" {
  security_group_id = aws_security_group.worker.id
  cidr_ipv4         = var.vpc_cidr
  from_port         = 53
  to_port           = 53
  ip_protocol       = "udp"
  description       = "DNS resolution within VPC (Cloud Map)"
}

resource "aws_vpc_security_group_egress_rule" "worker_dns_tcp" {
  security_group_id = aws_security_group.worker.id
  cidr_ipv4         = var.vpc_cidr
  from_port         = 53
  to_port           = 53
  ip_protocol       = "tcp"
  description       = "DNS resolution within VPC (Cloud Map, TCP fallback)"
}
