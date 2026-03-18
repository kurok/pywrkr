# Multi-Region Distributed pywrkr on AWS ECS/Fargate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a production-ready Terraform + Jenkins infrastructure at `infra/multi-region/` that deploys distributed pywrkr load tests across multiple AWS regions with controlled multi-source-IP egress via per-subnet NAT gateways on ECS Fargate.

**Architecture:** Single Terraform root module with a shared ECR layer and static per-region module blocks (count-gated). Each region gets an independent "test cell" — VPC, ECS cluster, Cloud Map namespace, master service, N worker services pinned to individual subnets for source-IP control. Jenkins orchestrates build/deploy/run/collect/cleanup across all regions in parallel.

**Tech Stack:** Terraform 1.6+ (AWS provider ~>5.0), Jenkins declarative pipeline, Docker (python:3.13-slim), AWS ECS Fargate, Cloud Map, ECR with cross-region replication, NAT gateways + Elastic IPs.

**Spec:** `docs/superpowers/specs/2026-03-18-multi-region-pywrkr-design.md`

**Existing patterns to follow:** See `infra/terraform/` for naming conventions. Key patterns:
- `name_prefix = "${var.project_name}-${var.environment}"` for all resource names
- `default_tags` in provider: `Project`, `Environment`, `ManagedBy`
- Per-resource `Name` tag using `name_prefix`
- Module inputs always include `name_prefix` and `tags`
- Container commands built via `locals` with conditional segments concatenated
- Master wraps pywrkr in `sh -c` to capture JSON results with `---PYWRKR_JSON_START---`/`---PYWRKR_JSON_END---` markers
- Worker uses direct command array: `["--worker", "pywrkr-master.${namespace}:9220"]`
- Log config uses `awslogs` driver with `/ecs/${name_prefix}/master` or `/worker` group paths
- Thresholds passed as list of strings, flattened with `["--threshold", th]`
- Port: use `9220` (correct default), not `9000` (existing infra bug)

---

## File Structure

All files live under `infra/multi-region/`. No files outside this directory are created or modified.

```
infra/multi-region/
├── Dockerfile                                    # Task 1
├── scenarios/
│   ├── simple-get.json                           # Task 1
│   ├── api-flow.json                             # Task 1
│   └── har-example.json                          # Task 1
├── terraform/
│   ├── versions.tf                               # Task 2
│   ├── backend.tf                                # Task 2
│   ├── providers.tf                              # Task 2
│   ├── variables.tf                              # Task 2
│   ├── locals.tf                                 # Task 2
│   ├── terraform.tfvars.example                  # Task 13
│   ├── main.tf                                   # Task 12
│   ├── outputs.tf                                # Task 12
│   └── modules/
│       ├── shared/
│       │   ├── variables.tf                      # Task 3
│       │   ├── main.tf                           # Task 3
│       │   └── outputs.tf                        # Task 3
│       ├── network/
│       │   ├── variables.tf                      # Task 4
│       │   ├── main.tf                           # Task 4
│       │   └── outputs.tf                        # Task 4
│       ├── iam/
│       │   ├── variables.tf                      # Task 5
│       │   ├── main.tf                           # Task 5
│       │   └── outputs.tf                        # Task 5
│       ├── ecs-cluster/
│       │   ├── variables.tf                      # Task 6
│       │   ├── main.tf                           # Task 6
│       │   └── outputs.tf                        # Task 6
│       ├── logging/
│       │   ├── variables.tf                      # Task 7
│       │   ├── main.tf                           # Task 7
│       │   └── outputs.tf                        # Task 7
│       ├── observability/
│       │   ├── variables.tf                      # Task 8
│       │   ├── main.tf                           # Task 8
│       │   └── outputs.tf                        # Task 8
│       ├── pywrkr-master/
│       │   ├── variables.tf                      # Task 9
│       │   ├── main.tf                           # Task 9
│       │   └── outputs.tf                        # Task 9
│       ├── pywrkr-worker/
│       │   ├── variables.tf                      # Task 10
│       │   ├── main.tf                           # Task 10
│       │   └── outputs.tf                        # Task 10
│       └── regional/
│           ├── variables.tf                      # Task 11
│           ├── main.tf                           # Task 11
│           └── outputs.tf                        # Task 11
├── jenkins/
│   └── Jenkinsfile                               # Task 14
└── README.md                                     # Task 15
```

---

## Task 1: Scaffold, Dockerfile, and Scenario Files

**Files:**
- Create: `infra/multi-region/Dockerfile`
- Create: `infra/multi-region/scenarios/simple-get.json`
- Create: `infra/multi-region/scenarios/api-flow.json`
- Create: `infra/multi-region/scenarios/har-example.json`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p infra/multi-region/{terraform/modules/{shared,network,iam,ecs-cluster,logging,observability,pywrkr-master,pywrkr-worker,regional},jenkins,scenarios}
```

- [ ] **Step 2: Create Dockerfile**

Create `infra/multi-region/Dockerfile`:

```dockerfile
FROM python:3.13-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
RUN pip install --no-cache-dir build && python -m build --wheel

FROM python:3.13-slim

COPY --from=builder /build/dist/*.whl /tmp/
# Install with [otel] extra for OpenTelemetry/Prometheus observability support.
# Excludes [tui], [dev], [lint], [security] extras not needed in production image.
# pip handles the glob internally; [otel] is a PEP 508 extra specifier.
RUN pip install --no-cache-dir "/tmp/*.whl[otel]" && rm /tmp/*.whl

COPY infra/multi-region/scenarios/ /scenarios/

ENTRYPOINT ["pywrkr"]
```

- [ ] **Step 3: Create simple-get.json scenario**

Create `infra/multi-region/scenarios/simple-get.json`:

```json
{
  "name": "Simple GET benchmark",
  "description": "Basic GET test against a single URL with SLO thresholds",
  "steps": [
    {
      "name": "homepage",
      "path": "/",
      "method": "GET",
      "headers": {
        "Accept": "text/html,application/json"
      }
    }
  ]
}
```

Note: Uses `path` (not `url`) because pywrkr scenario format uses relative paths with `base_url` set from the target URL.

- [ ] **Step 4: Create api-flow.json scenario**

Create `infra/multi-region/scenarios/api-flow.json`:

```json
{
  "name": "API endpoint multi-step test",
  "description": "Tests multiple API endpoints in sequence per virtual user iteration",
  "think_time": 1.0,
  "steps": [
    {
      "name": "health_check",
      "path": "/health",
      "method": "GET",
      "headers": {
        "Accept": "application/json"
      },
      "assert_status": 200
    },
    {
      "name": "list_items",
      "path": "/api/v1/items?limit=10",
      "method": "GET",
      "headers": {
        "Accept": "application/json"
      },
      "assert_status": 200,
      "think_time": 0.5
    },
    {
      "name": "create_item",
      "path": "/api/v1/items",
      "method": "POST",
      "headers": {
        "Content-Type": "application/json"
      },
      "body": {"name": "load-test-item", "value": 42},
      "assert_status": 201,
      "think_time": 1.0
    },
    {
      "name": "get_created_item",
      "path": "/api/v1/items/1",
      "method": "GET",
      "headers": {
        "Accept": "application/json"
      },
      "assert_status": 200
    }
  ]
}
```

- [ ] **Step 5: Create har-example.json scenario**

Create `infra/multi-region/scenarios/har-example.json`:

```json
{
  "name": "HAR-imported user flow",
  "description": "Example scenario generated from a browser HAR recording via: pywrkr har-import recording.har -o har-example.json",
  "think_time": 0.5,
  "steps": [
    {
      "name": "landing_page",
      "path": "/",
      "method": "GET",
      "headers": {
        "Accept": "text/html"
      },
      "think_time": 2.0
    },
    {
      "name": "login",
      "path": "/api/auth/login",
      "method": "POST",
      "headers": {
        "Content-Type": "application/json"
      },
      "body": {"username": "testuser", "password": "testpass"},
      "assert_status": 200,
      "think_time": 1.5
    },
    {
      "name": "dashboard",
      "path": "/api/dashboard",
      "method": "GET",
      "headers": {
        "Accept": "application/json"
      },
      "assert_status": 200,
      "think_time": 3.0
    },
    {
      "name": "search",
      "path": "/api/search?q=test&limit=20",
      "method": "GET",
      "headers": {
        "Accept": "application/json"
      },
      "assert_status": 200
    }
  ]
}
```

- [ ] **Step 6: Verify Dockerfile builds**

```bash
cd /path/to/pywrkr
docker build -f infra/multi-region/Dockerfile -t pywrkr:multi-region-test .
docker run --rm pywrkr:multi-region-test --help
```

Expected: pywrkr help output. Verify `--otel-endpoint` flag is present (confirms [otel] extra installed).

- [ ] **Step 7: Commit**

```bash
git add infra/multi-region/Dockerfile infra/multi-region/scenarios/
git commit -m "feat: add multi-region Dockerfile and scenario files"
```

---

## Task 2: Terraform Foundation Files

**Files:**
- Create: `infra/multi-region/terraform/versions.tf`
- Create: `infra/multi-region/terraform/backend.tf`
- Create: `infra/multi-region/terraform/providers.tf`
- Create: `infra/multi-region/terraform/variables.tf`
- Create: `infra/multi-region/terraform/locals.tf`

- [ ] **Step 1: Create versions.tf**

Create `infra/multi-region/terraform/versions.tf`:

```hcl
terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
```

- [ ] **Step 2: Create backend.tf**

Create `infra/multi-region/terraform/backend.tf`:

```hcl
# Default: local state file. Suitable for single-operator or ephemeral usage.
# For team usage, uncomment the S3 backend below and create the bucket/table first.

# terraform {
#   backend "s3" {
#     bucket         = "your-terraform-state-bucket"
#     key            = "pywrkr/multi-region/terraform.tfstate"
#     region         = "us-east-1"
#     dynamodb_table = "terraform-state-lock"
#     encrypt        = true
#   }
# }
```

- [ ] **Step 3: Create providers.tf**

Create `infra/multi-region/terraform/providers.tf`:

```hcl
# -----------------------------------------------------------------------------
# Provider aliases — one per supported region.
# Terraform requires static provider aliases; they cannot be generated
# dynamically with for_each. To support a new region, add a provider block
# here and a corresponding module block in main.tf.
# -----------------------------------------------------------------------------

# Home region — used for ECR repository and as the default provider.
provider "aws" {
  region = var.home_region

  default_tags {
    tags = {
      Project     = "pywrkr"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# --- Regional provider aliases ---

provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = "pywrkr"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

provider "aws" {
  alias  = "eu_west_1"
  region = "eu-west-1"

  default_tags {
    tags = {
      Project     = "pywrkr"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

provider "aws" {
  alias  = "ap_southeast_1"
  region = "ap-southeast-1"

  default_tags {
    tags = {
      Project     = "pywrkr"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
```

- [ ] **Step 4: Create variables.tf**

Create `infra/multi-region/terraform/variables.tf`:

```hcl
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
```

- [ ] **Step 5: Create locals.tf**

Create `infra/multi-region/terraform/locals.tf`:

```hcl
locals {
  name_prefix = "${var.project_name}-${var.environment}"

  # Collect enabled regions for outputs and shared module
  enabled_regions = {
    for region, config in var.regions : region => config
    if config.enabled
  }

  # List of region names for ECR replication targets (exclude home region)
  ecr_replication_regions = [
    for region in keys(local.enabled_regions) : region
    if region != var.home_region
  ]

  # Common tags passed to modules (in addition to provider default_tags)
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}
```

- [ ] **Step 6: Validate Terraform syntax**

```bash
cd infra/multi-region/terraform
terraform fmt -recursive -check
```

Expected: All files formatted correctly. If not, run `terraform fmt -recursive` and fix.

Note: `terraform validate` will fail at this stage because modules referenced in main.tf don't exist yet. That's expected — we validate individual modules as we build them and run full validation in Task 12.

- [ ] **Step 7: Commit**

```bash
git add infra/multi-region/terraform/versions.tf infra/multi-region/terraform/backend.tf infra/multi-region/terraform/providers.tf infra/multi-region/terraform/variables.tf infra/multi-region/terraform/locals.tf
git commit -m "feat: add multi-region Terraform foundation files"
```

---

## Task 3: Shared Module (ECR + Replication)

**Files:**
- Create: `infra/multi-region/terraform/modules/shared/variables.tf`
- Create: `infra/multi-region/terraform/modules/shared/main.tf`
- Create: `infra/multi-region/terraform/modules/shared/outputs.tf`

- [ ] **Step 1: Create shared/variables.tf**

```hcl
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
```

- [ ] **Step 2: Create shared/main.tf**

```hcl
# -----------------------------------------------------------------------------
# ECR Repository — created in the home region (default provider)
# -----------------------------------------------------------------------------

resource "aws_ecr_repository" "pywrkr" {
  name                 = var.ecr_repo_name
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-ecr"
  })
}

resource "aws_ecr_lifecycle_policy" "pywrkr" {
  repository = aws_ecr_repository.pywrkr.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 20 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 20
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# ECR Replication — push images to target regions automatically
# -----------------------------------------------------------------------------

resource "aws_ecr_replication_configuration" "cross_region" {
  count = length(var.replication_regions) > 0 ? 1 : 0

  replication_configuration {
    rule {
      dynamic "destination" {
        for_each = var.replication_regions
        content {
          region      = destination.value
          registry_id = aws_ecr_repository.pywrkr.registry_id
        }
      }

      repository_filter {
        filter      = var.ecr_repo_name
        filter_type = "PREFIX_MATCH"
      }
    }
  }
}
```

- [ ] **Step 3: Create shared/outputs.tf**

```hcl
output "ecr_repository_url" {
  description = "ECR repository URL in the home region"
  value       = aws_ecr_repository.pywrkr.repository_url
}

output "ecr_repository_arn" {
  description = "ECR repository ARN"
  value       = aws_ecr_repository.pywrkr.arn
}

output "ecr_registry_id" {
  description = "ECR registry ID (AWS account ID)"
  value       = aws_ecr_repository.pywrkr.registry_id
}
```

- [ ] **Step 4: Validate module formatting**

```bash
cd infra/multi-region/terraform/modules/shared
terraform fmt -check
```

- [ ] **Step 5: Commit**

```bash
git add infra/multi-region/terraform/modules/shared/
git commit -m "feat: add shared module with ECR and cross-region replication"
```

---

## Task 4: Network Module

This is the most complex module — VPC, subnets (public, private master, private worker), NAT gateways, EIPs, route tables, and security groups. The worker subnet count matches the desired number of source IPs.

**Files:**
- Create: `infra/multi-region/terraform/modules/network/variables.tf`
- Create: `infra/multi-region/terraform/modules/network/main.tf`
- Create: `infra/multi-region/terraform/modules/network/outputs.tf`

- [ ] **Step 1: Create network/variables.tf**

```hcl
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
```

- [ ] **Step 2: Create network/main.tf**

```hcl
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
```

- [ ] **Step 3: Create network/outputs.tf**

```hcl
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
```

- [ ] **Step 4: Validate formatting**

```bash
terraform fmt -check infra/multi-region/terraform/modules/network/
```

- [ ] **Step 5: Commit**

```bash
git add infra/multi-region/terraform/modules/network/
git commit -m "feat: add network module with multi-NAT source IP isolation"
```

---

## Task 5: IAM Module

**Files:**
- Create: `infra/multi-region/terraform/modules/iam/variables.tf`
- Create: `infra/multi-region/terraform/modules/iam/main.tf`
- Create: `infra/multi-region/terraform/modules/iam/outputs.tf`

- [ ] **Step 1: Create iam/variables.tf**

```hcl
variable "name_prefix" {
  description = "Resource name prefix"
  type        = string
}

variable "tags" {
  description = "Additional resource tags"
  type        = map(string)
  default     = {}
}
```

- [ ] **Step 2: Create iam/main.tf**

```hcl
# -----------------------------------------------------------------------------
# ECS Task Execution Role — used by ECS agent to pull images and write logs
# -----------------------------------------------------------------------------

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name_prefix}-ecs-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-ecs-exec"
  })
}

resource "aws_iam_role_policy_attachment" "execution_ecr_logs" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# -----------------------------------------------------------------------------
# ECS Task Role — used by the running container for AWS API calls
# -----------------------------------------------------------------------------

resource "aws_iam_role" "task" {
  name               = "${var.name_prefix}-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-ecs-task"
  })
}

# Allow ECS Exec for debugging
resource "aws_iam_role_policy" "task_ecs_exec" {
  name = "${var.name_prefix}-ecs-exec-policy"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssmmessages:CreateControlChannel",
          "ssmmessages:CreateDataChannel",
          "ssmmessages:OpenControlChannel",
          "ssmmessages:OpenDataChannel"
        ]
        Resource = "*"
      }
    ]
  })
}
```

- [ ] **Step 3: Create iam/outputs.tf**

```hcl
output "execution_role_arn" {
  description = "ECS task execution role ARN"
  value       = aws_iam_role.execution.arn
}

output "task_role_arn" {
  description = "ECS task role ARN"
  value       = aws_iam_role.task.arn
}
```

- [ ] **Step 4: Commit**

```bash
git add infra/multi-region/terraform/modules/iam/
git commit -m "feat: add IAM module for ECS task roles"
```

---

## Task 6: ECS Cluster + Cloud Map Module

**Files:**
- Create: `infra/multi-region/terraform/modules/ecs-cluster/variables.tf`
- Create: `infra/multi-region/terraform/modules/ecs-cluster/main.tf`
- Create: `infra/multi-region/terraform/modules/ecs-cluster/outputs.tf`

- [ ] **Step 1: Create ecs-cluster/variables.tf**

```hcl
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
```

- [ ] **Step 2: Create ecs-cluster/main.tf**

```hcl
resource "aws_ecs_cluster" "main" {
  name = "${var.name_prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-cluster"
  })
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name = aws_ecs_cluster.main.name

  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 1
  }
}

resource "aws_service_discovery_private_dns_namespace" "main" {
  name        = var.cloudmap_namespace
  description = "pywrkr service discovery namespace"
  vpc         = var.vpc_id

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-namespace"
  })
}
```

- [ ] **Step 3: Create ecs-cluster/outputs.tf**

```hcl
output "cluster_id" {
  description = "ECS cluster ID"
  value       = aws_ecs_cluster.main.id
}

output "cluster_arn" {
  description = "ECS cluster ARN"
  value       = aws_ecs_cluster.main.arn
}

output "cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.main.name
}

output "namespace_id" {
  description = "Cloud Map namespace ID"
  value       = aws_service_discovery_private_dns_namespace.main.id
}

output "namespace_arn" {
  description = "Cloud Map namespace ARN"
  value       = aws_service_discovery_private_dns_namespace.main.arn
}

output "namespace_name" {
  description = "Cloud Map namespace DNS name"
  value       = aws_service_discovery_private_dns_namespace.main.name
}
```

- [ ] **Step 4: Commit**

```bash
git add infra/multi-region/terraform/modules/ecs-cluster/
git commit -m "feat: add ECS cluster module with Cloud Map namespace"
```

---

## Task 7: Logging Module

**Files:**
- Create: `infra/multi-region/terraform/modules/logging/variables.tf`
- Create: `infra/multi-region/terraform/modules/logging/main.tf`
- Create: `infra/multi-region/terraform/modules/logging/outputs.tf`

- [ ] **Step 1: Create logging/variables.tf**

```hcl
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
```

- [ ] **Step 2: Create logging/main.tf**

```hcl
resource "aws_cloudwatch_log_group" "master" {
  name              = "/ecs/${var.name_prefix}/master"
  retention_in_days = var.retention_days

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-master-logs"
    Role = "master"
  })
}

# Single shared worker log group — individual worker services are
# differentiated by the awslogs-stream-prefix which includes the
# service name (worker-0, worker-1, etc.)
resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${var.name_prefix}/worker"
  retention_in_days = var.retention_days

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-worker-logs"
    Role = "worker"
  })
}
```

- [ ] **Step 3: Create logging/outputs.tf**

```hcl
output "master_log_group_name" {
  description = "Master CloudWatch log group name"
  value       = aws_cloudwatch_log_group.master.name
}

output "master_log_group_arn" {
  description = "Master CloudWatch log group ARN"
  value       = aws_cloudwatch_log_group.master.arn
}

output "worker_log_group_name" {
  description = "Worker CloudWatch log group name (shared by all worker services)"
  value       = aws_cloudwatch_log_group.worker.name
}

output "worker_log_group_arn" {
  description = "Worker CloudWatch log group ARN"
  value       = aws_cloudwatch_log_group.worker.arn
}
```

- [ ] **Step 4: Commit**

```bash
git add infra/multi-region/terraform/modules/logging/
git commit -m "feat: add logging module for CloudWatch log groups"
```

---

## Task 8: Observability Module

**Files:**
- Create: `infra/multi-region/terraform/modules/observability/variables.tf`
- Create: `infra/multi-region/terraform/modules/observability/main.tf`
- Create: `infra/multi-region/terraform/modules/observability/outputs.tf`

- [ ] **Step 1: Create observability/variables.tf**

```hcl
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
```

- [ ] **Step 2: Create observability/main.tf**

```hcl
# -----------------------------------------------------------------------------
# VPC Flow Logs — optional, for network troubleshooting
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "flow_logs" {
  count = var.enable_flow_logs ? 1 : 0

  name              = "/vpc/${var.name_prefix}/flow-logs"
  retention_in_days = var.flow_log_retention_days

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-flow-logs"
  })
}

data "aws_iam_policy_document" "flow_logs_assume" {
  count = var.enable_flow_logs ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "flow_logs" {
  count = var.enable_flow_logs ? 1 : 0

  name               = "${var.name_prefix}-flow-logs"
  assume_role_policy = data.aws_iam_policy_document.flow_logs_assume[0].json

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-flow-logs-role"
  })
}

resource "aws_iam_role_policy" "flow_logs" {
  count = var.enable_flow_logs ? 1 : 0

  name = "${var.name_prefix}-flow-logs-policy"
  role = aws_iam_role.flow_logs[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_flow_log" "vpc" {
  count = var.enable_flow_logs ? 1 : 0

  vpc_id               = var.vpc_id
  traffic_type         = "ALL"
  iam_role_arn         = aws_iam_role.flow_logs[0].arn
  log_destination      = aws_cloudwatch_log_group.flow_logs[0].arn
  log_destination_type = "cloud-watch-logs"
  max_aggregation_interval = 60

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-flow-log"
  })
}
```

- [ ] **Step 3: Create observability/outputs.tf**

```hcl
output "flow_log_id" {
  description = "VPC Flow Log ID (empty if disabled)"
  value       = try(aws_flow_log.vpc[0].id, "")
}

output "flow_log_group_name" {
  description = "Flow Log CloudWatch log group name (empty if disabled)"
  value       = try(aws_cloudwatch_log_group.flow_logs[0].name, "")
}
```

- [ ] **Step 4: Commit**

```bash
git add infra/multi-region/terraform/modules/observability/
git commit -m "feat: add observability module with VPC flow logs"
```

---

## Task 9: pywrkr Master Module

**Files:**
- Create: `infra/multi-region/terraform/modules/pywrkr-master/variables.tf`
- Create: `infra/multi-region/terraform/modules/pywrkr-master/main.tf`
- Create: `infra/multi-region/terraform/modules/pywrkr-master/outputs.tf`

- [ ] **Step 1: Create pywrkr-master/variables.tf**

```hcl
variable "name_prefix" {
  description = "Resource name prefix"
  type        = string
}

variable "cluster_id" {
  description = "ECS cluster ID"
  type        = string
}

variable "namespace_id" {
  description = "Cloud Map namespace ID for service registration"
  type        = string
}

variable "image" {
  description = "Docker image URI including tag (e.g., 123456.dkr.ecr.us-east-1.amazonaws.com/pywrkr:v1)"
  type        = string
}

variable "cpu" {
  description = "Task CPU units"
  type        = number
  default     = 1024
}

variable "memory" {
  description = "Task memory in MB"
  type        = number
  default     = 2048
}

variable "execution_role_arn" {
  description = "ECS task execution role ARN"
  type        = string
}

variable "task_role_arn" {
  description = "ECS task role ARN"
  type        = string
}

variable "subnet_id" {
  description = "Subnet ID for master task"
  type        = string
}

variable "security_group_id" {
  description = "Security group ID for master task"
  type        = string
}

variable "assign_public_ip" {
  description = "Assign public IP to master task (true in public_ip mode)"
  type        = bool
  default     = false
}

variable "log_group_name" {
  description = "CloudWatch log group name"
  type        = string
}

variable "aws_region" {
  description = "AWS region for log configuration"
  type        = string
}

variable "coordination_port" {
  description = "TCP port for master/worker coordination"
  type        = number
  default     = 9220
}

variable "cloudmap_namespace" {
  description = "Cloud Map namespace name for DNS"
  type        = string
}

# --- Test configuration ---

variable "worker_count" {
  description = "Number of workers the master should expect"
  type        = number
}

variable "target_url" {
  description = "Target URL to benchmark"
  type        = string
}

variable "test_duration" {
  description = "Test duration in seconds"
  type        = number
}

variable "users" {
  description = "Virtual users per worker (0 = connection-based mode)"
  type        = number
  default     = 0
}

variable "connections" {
  description = "Concurrent connections per worker"
  type        = number
  default     = 10
}

variable "rate" {
  description = "Request rate per worker (0 = unlimited)"
  type        = number
  default     = 0
}

variable "thresholds" {
  description = "List of threshold expressions"
  type        = list(string)
  default     = []
}

variable "scenario_file" {
  description = "Scenario file path inside container (empty = simple URL mode)"
  type        = string
  default     = ""
}

variable "pywrkr_tags" {
  description = "Tags to pass to pywrkr --tag"
  type        = map(string)
  default     = {}
}

variable "otel_endpoint" {
  description = "OTel collector endpoint"
  type        = string
  default     = ""
}

variable "prom_remote_write" {
  description = "Prometheus pushgateway URL"
  type        = string
  default     = ""
}

variable "tags" {
  description = "Additional resource tags"
  type        = map(string)
  default     = {}
}
```

- [ ] **Step 2: Create pywrkr-master/main.tf**

```hcl
# -----------------------------------------------------------------------------
# Command construction — mirrors existing infra pattern
# Wraps pywrkr in sh -c to capture JSON results with markers for log parsing.
# -----------------------------------------------------------------------------

locals {
  base_cmd       = ["--master", "--expect-workers", tostring(var.worker_count), "--bind", "0.0.0.0", "--port", tostring(var.coordination_port)]
  duration_cmd   = ["-d", tostring(var.test_duration)]
  connections_cmd = var.users == 0 ? ["-c", tostring(var.connections)] : []
  users_cmd      = var.users > 0 ? ["-u", tostring(var.users)] : []
  rate_cmd       = var.rate > 0 ? ["--rate", tostring(var.rate)] : []
  threshold_cmd  = flatten([for th in var.thresholds : ["--threshold", th]])
  tag_cmd        = flatten([for k, v in var.pywrkr_tags : ["--tag", "${k}=${v}"]])
  otel_cmd       = var.otel_endpoint != "" ? ["--otel-endpoint", var.otel_endpoint] : []
  prom_cmd       = var.prom_remote_write != "" ? ["--prom-remote-write", var.prom_remote_write] : []
  scenario_cmd   = var.scenario_file != "" ? ["--scenario", var.scenario_file] : []
  url_cmd        = var.scenario_file == "" ? [var.target_url] : []

  pywrkr_args = concat(
    local.base_cmd,
    local.duration_cmd,
    local.connections_cmd,
    local.users_cmd,
    local.rate_cmd,
    local.threshold_cmd,
    local.tag_cmd,
    local.otel_cmd,
    local.prom_cmd,
    local.scenario_cmd,
    local.url_cmd,
  )

  # Wrap in shell script to capture JSON results with markers for CloudWatch log parsing
  master_shell_script = join(" ", concat(
    ["pywrkr"],
    local.pywrkr_args,
    ["--json", "/tmp/results.json", ";"],
    ["EXIT_CODE=$?;"],
    ["echo '---PYWRKR_JSON_START---';"],
    ["cat /tmp/results.json;"],
    ["echo '---PYWRKR_JSON_END---';"],
    ["exit $EXIT_CODE"],
  ))
}

# -----------------------------------------------------------------------------
# Task Definition
# -----------------------------------------------------------------------------

resource "aws_ecs_task_definition" "master" {
  family                   = "${var.name_prefix}-master"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name       = "pywrkr-master"
      image      = var.image
      essential  = true
      entryPoint = ["sh", "-c"]
      command    = [local.master_shell_script]

      portMappings = [
        {
          containerPort = var.coordination_port
          protocol      = "tcp"
        }
      ]

      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import socket; s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1', ${var.coordination_port})); s.close()\""]
        interval    = 10
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.log_group_name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "master"
        }
      }

      environment = concat(
        [{ name = "PYWRKR_ROLE", value = "master" }],
        var.otel_endpoint != "" ? [{ name = "OTEL_EXPORTER_OTLP_ENDPOINT", value = var.otel_endpoint }] : [],
        var.prom_remote_write != "" ? [{ name = "PYWRKR_PROM_ENDPOINT", value = var.prom_remote_write }] : [],
      )
    }
  ])

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-master-task"
    Role = "master"
  })
}

# -----------------------------------------------------------------------------
# Cloud Map Service Registration
# -----------------------------------------------------------------------------

resource "aws_service_discovery_service" "master" {
  name = "pywrkr-master"

  dns_config {
    namespace_id = var.namespace_id

    dns_records {
      ttl  = 10
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {
    failure_threshold = 1
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-master-discovery"
  })
}

# -----------------------------------------------------------------------------
# ECS Service
# -----------------------------------------------------------------------------

resource "aws_ecs_service" "master" {
  name            = "${var.name_prefix}-master"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.master.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  enable_execute_command             = true

  network_configuration {
    subnets          = [var.subnet_id]
    security_groups  = [var.security_group_id]
    assign_public_ip = var.assign_public_ip
  }

  service_registries {
    registry_arn = aws_service_discovery_service.master.arn
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-master-service"
    Role = "master"
  })
}
```

- [ ] **Step 3: Create pywrkr-master/outputs.tf**

```hcl
output "service_name" {
  description = "Master ECS service name"
  value       = aws_ecs_service.master.name
}

output "service_arn" {
  description = "Master ECS service ARN"
  value       = aws_ecs_service.master.id
}

output "task_definition_arn" {
  description = "Master task definition ARN"
  value       = aws_ecs_task_definition.master.arn
}

output "dns_name" {
  description = "Master Cloud Map DNS name"
  value       = "pywrkr-master.${var.cloudmap_namespace}"
}

output "discovery_service_arn" {
  description = "Cloud Map service ARN"
  value       = aws_service_discovery_service.master.arn
}
```

- [ ] **Step 4: Commit**

```bash
git add infra/multi-region/terraform/modules/pywrkr-master/
git commit -m "feat: add pywrkr master module with Cloud Map service discovery"
```

---

## Task 10: pywrkr Worker Module

Creates N worker ECS services, each pinned to a specific subnet for source-IP control.

**Files:**
- Create: `infra/multi-region/terraform/modules/pywrkr-worker/variables.tf`
- Create: `infra/multi-region/terraform/modules/pywrkr-worker/main.tf`
- Create: `infra/multi-region/terraform/modules/pywrkr-worker/outputs.tf`

- [ ] **Step 1: Create pywrkr-worker/variables.tf**

```hcl
variable "name_prefix" {
  description = "Resource name prefix"
  type        = string
}

variable "cluster_id" {
  description = "ECS cluster ID"
  type        = string
}

variable "image" {
  description = "Docker image URI including tag"
  type        = string
}

variable "cpu" {
  description = "Task CPU units"
  type        = number
  default     = 1024
}

variable "memory" {
  description = "Task memory in MB"
  type        = number
  default     = 2048
}

variable "execution_role_arn" {
  description = "ECS task execution role ARN"
  type        = string
}

variable "task_role_arn" {
  description = "ECS task role ARN"
  type        = string
}

variable "worker_count" {
  description = "Number of worker services to create"
  type        = number
  default     = 3
}

variable "worker_subnet_ids" {
  description = "List of subnet IDs, one per worker service (for source IP pinning)"
  type        = list(string)
}

variable "security_group_id" {
  description = "Worker security group ID"
  type        = string
}

variable "assign_public_ip" {
  description = "Assign public IP (true in public_ip mode)"
  type        = bool
  default     = false
}

variable "log_group_name" {
  description = "CloudWatch log group name (shared by all worker services)"
  type        = string
}

variable "aws_region" {
  description = "AWS region for log configuration"
  type        = string
}

variable "master_dns" {
  description = "Master DNS name (e.g., pywrkr-master.pywrkr.local)"
  type        = string
}

variable "coordination_port" {
  description = "TCP port for master/worker coordination"
  type        = number
  default     = 9220
}

variable "tags" {
  description = "Additional resource tags"
  type        = map(string)
  default     = {}
}
```

- [ ] **Step 2: Create pywrkr-worker/main.tf**

```hcl
locals {
  worker_command = ["--worker", "${var.master_dns}:${var.coordination_port}"]
}

# -----------------------------------------------------------------------------
# Task Definition — shared by all worker services
# -----------------------------------------------------------------------------

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.name_prefix}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "pywrkr-worker"
      image     = var.image
      essential = true
      command   = local.worker_command

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.log_group_name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "worker"
        }
      }

      environment = [
        { name = "PYWRKR_ROLE", value = "worker" }
      ]
    }
  ])

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-worker-task"
    Role = "worker"
  })
}

# -----------------------------------------------------------------------------
# Worker ECS Services — one per subnet for source IP isolation
# Each service runs desired_count=1 in a specific subnet.
# In nat_eip mode, each subnet routes through a different NAT/EIP.
# -----------------------------------------------------------------------------

resource "aws_ecs_service" "worker" {
  count = var.worker_count

  name            = "${var.name_prefix}-worker-${count.index}"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  enable_execute_command             = true

  network_configuration {
    subnets          = [var.worker_subnet_ids[count.index]]
    security_groups  = [var.security_group_id]
    assign_public_ip = var.assign_public_ip
  }

  tags = merge(var.tags, {
    Name        = "${var.name_prefix}-worker-${count.index}-service"
    Role        = "worker"
    WorkerIndex = tostring(count.index)
  })
}
```

Note: All worker services share one task definition and one log group. Individual worker streams are differentiated by the `awslogs-stream-prefix` (which includes the ECS service name, e.g., `worker/pywrkr-loadtest-use1-worker-0/...`). If per-worker log isolation is needed later, create separate task definitions per worker.

- [ ] **Step 3: Create pywrkr-worker/outputs.tf**

```hcl
output "service_names" {
  description = "List of worker ECS service names"
  value       = aws_ecs_service.worker[*].name
}

output "service_arns" {
  description = "List of worker ECS service ARNs"
  value       = aws_ecs_service.worker[*].id
}

output "task_definition_arn" {
  description = "Worker task definition ARN (shared by all worker services)"
  value       = aws_ecs_task_definition.worker.arn
}
```

- [ ] **Step 4: Commit**

```bash
git add infra/multi-region/terraform/modules/pywrkr-worker/
git commit -m "feat: add pywrkr worker module with per-subnet IP pinning"
```

---

## Task 11: Regional Composition Module

Wires together all per-region modules into a single callable unit.

**Files:**
- Create: `infra/multi-region/terraform/modules/regional/variables.tf`
- Create: `infra/multi-region/terraform/modules/regional/main.tf`
- Create: `infra/multi-region/terraform/modules/regional/outputs.tf`

- [ ] **Step 1: Create regional/variables.tf**

```hcl
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
  type = string
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
```

- [ ] **Step 2: Create regional/main.tf**

```hcl
# -----------------------------------------------------------------------------
# Regional Test Cell — composes all per-region modules
# This module is called once per enabled region from the root main.tf.
# -----------------------------------------------------------------------------

module "network" {
  source = "../network"

  name_prefix         = var.name_prefix
  vpc_cidr            = var.vpc_cidr
  az_count            = var.az_count
  worker_subnet_count = var.worker_count
  egress_mode         = var.egress_mode
  tags                = var.tags
}

module "iam" {
  source = "../iam"

  name_prefix = var.name_prefix
  tags        = var.tags
}

module "ecs_cluster" {
  source = "../ecs-cluster"

  name_prefix        = var.name_prefix
  cloudmap_namespace = var.cloudmap_namespace
  vpc_id             = module.network.vpc_id
  tags               = var.tags
}

module "logging" {
  source = "../logging"

  name_prefix    = var.name_prefix
  retention_days = var.log_retention_days
  worker_count   = var.worker_count
  tags           = var.tags
}

module "observability" {
  source = "../observability"

  name_prefix             = var.name_prefix
  vpc_id                  = module.network.vpc_id
  enable_flow_logs        = var.enable_flow_logs
  flow_log_retention_days = var.flow_log_retention_days
  tags                    = var.tags
}

module "master" {
  source = "../pywrkr-master"

  name_prefix        = var.name_prefix
  cluster_id         = module.ecs_cluster.cluster_id
  namespace_id       = module.ecs_cluster.namespace_id
  image              = var.image
  cpu                = var.master_cpu
  memory             = var.master_memory
  execution_role_arn = module.iam.execution_role_arn
  task_role_arn      = module.iam.task_role_arn
  subnet_id          = var.egress_mode == "nat_eip" ? module.network.master_subnet_id : module.network.public_subnet_ids[0]
  security_group_id  = module.network.master_sg_id
  assign_public_ip   = var.egress_mode == "public_ip"
  log_group_name     = module.logging.master_log_group_name
  aws_region         = var.region_name
  cloudmap_namespace = var.cloudmap_namespace
  worker_count       = var.worker_count
  target_url         = var.target_url
  test_duration      = var.test_duration
  users              = var.users
  connections        = var.connections
  rate               = var.rate
  thresholds         = var.thresholds
  scenario_file      = var.scenario_file
  pywrkr_tags        = var.pywrkr_tags
  otel_endpoint      = var.otel_endpoint
  prom_remote_write  = var.prom_remote_write
  tags               = var.tags
}

module "workers" {
  source = "../pywrkr-worker"

  name_prefix        = var.name_prefix
  cluster_id         = module.ecs_cluster.cluster_id
  image              = var.image
  cpu                = var.worker_cpu
  memory             = var.worker_memory
  execution_role_arn = module.iam.execution_role_arn
  task_role_arn      = module.iam.task_role_arn
  worker_count       = var.worker_count
  worker_subnet_ids  = var.egress_mode == "nat_eip" ? module.network.worker_subnet_ids : slice(concat(module.network.public_subnet_ids, module.network.public_subnet_ids, module.network.public_subnet_ids), 0, var.worker_count)
  security_group_id  = module.network.worker_sg_id
  assign_public_ip   = var.egress_mode == "public_ip"
  log_group_name     = module.logging.worker_log_group_name
  aws_region         = var.region_name
  master_dns         = module.master.dns_name
  tags               = var.tags

  depends_on = [module.master]
}
```

- [ ] **Step 3: Create regional/outputs.tf**

```hcl
output "cluster_name" {
  value = module.ecs_cluster.cluster_name
}

output "cluster_arn" {
  value = module.ecs_cluster.cluster_arn
}

output "master_service_name" {
  value = module.master.service_name
}

output "master_dns_name" {
  value = module.master.dns_name
}

output "worker_service_names" {
  value = module.workers.service_names
}

output "master_log_group_name" {
  value = module.logging.master_log_group_name
}

output "worker_log_group_name" {
  value = module.logging.worker_log_group_name
}

output "nat_eips" {
  value = module.network.nat_eips
}

output "namespace_name" {
  value = module.ecs_cluster.namespace_name
}

output "vpc_id" {
  value = module.network.vpc_id
}
```

- [ ] **Step 4: Commit**

```bash
git add infra/multi-region/terraform/modules/regional/
git commit -m "feat: add regional composition module"
```

---

## Task 12: Root main.tf and outputs.tf

Wires the shared module and static per-region module blocks together.

**Files:**
- Create: `infra/multi-region/terraform/main.tf`
- Create: `infra/multi-region/terraform/outputs.tf`

- [ ] **Step 1: Create main.tf**

```hcl
# -----------------------------------------------------------------------------
# Root module — shared resources + static per-region module blocks
#
# Terraform does not support dynamic provider aliases in for_each, so each
# supported region gets a static module block gated by count.
# To add a new region: add a provider alias in providers.tf and a module block here.
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Shared layer — ECR repository + cross-region replication
# -----------------------------------------------------------------------------

module "shared" {
  source = "./modules/shared"

  name_prefix         = local.name_prefix
  ecr_repo_name       = "${local.name_prefix}-pywrkr"
  replication_regions = local.ecr_replication_regions
  tags                = local.common_tags
}

# -----------------------------------------------------------------------------
# Regional test cells — one module block per supported region
# Enable/disable via var.regions["<region>"].enabled
# -----------------------------------------------------------------------------

locals {
  # ECR image URI — each region has its own ECR endpoint after replication
  ecr_image_base = module.shared.ecr_repository_url
  ecr_image      = "${local.ecr_image_base}:${var.image_tag}"

  # Per-region pywrkr tags include the region name
  region_pywrkr_tags = {
    for region in keys(local.enabled_regions) : region => merge(var.tags, {
      region      = region
      environment = var.environment
    })
  }
}

# --- us-east-1 ---

module "region_us_east_1" {
  count  = try(var.regions["us-east-1"].enabled, false) ? 1 : 0
  source = "./modules/regional"
  providers = {
    aws = aws.us_east_1
  }

  name_prefix             = "${local.name_prefix}-use1"
  region_name             = "us-east-1"
  vpc_cidr                = var.regions["us-east-1"].vpc_cidr
  az_count                = var.regions["us-east-1"].az_count
  worker_count            = var.regions["us-east-1"].worker_count
  egress_mode             = var.egress_mode
  enable_flow_logs        = var.enable_flow_logs
  flow_log_retention_days = var.flow_log_retention_days
  cloudmap_namespace      = var.cloudmap_namespace
  image                   = local.ecr_image
  master_cpu              = var.regions["us-east-1"].master_cpu
  master_memory           = var.regions["us-east-1"].master_memory
  worker_cpu              = var.regions["us-east-1"].worker_cpu
  worker_memory           = var.regions["us-east-1"].worker_memory
  target_url              = var.target_url
  test_duration           = var.test_duration
  users                   = var.users
  connections             = var.connections
  rate                    = var.rate
  thresholds              = var.thresholds
  scenario_file           = var.scenario_file
  pywrkr_tags             = local.region_pywrkr_tags["us-east-1"]
  otel_endpoint           = var.otel_endpoint
  prom_remote_write       = var.prom_remote_write
  log_retention_days      = var.log_retention_days
  tags                    = local.common_tags
}

# --- eu-west-1 ---

module "region_eu_west_1" {
  count  = try(var.regions["eu-west-1"].enabled, false) ? 1 : 0
  source = "./modules/regional"
  providers = {
    aws = aws.eu_west_1
  }

  name_prefix             = "${local.name_prefix}-euw1"
  region_name             = "eu-west-1"
  vpc_cidr                = var.regions["eu-west-1"].vpc_cidr
  az_count                = var.regions["eu-west-1"].az_count
  worker_count            = var.regions["eu-west-1"].worker_count
  egress_mode             = var.egress_mode
  enable_flow_logs        = var.enable_flow_logs
  flow_log_retention_days = var.flow_log_retention_days
  cloudmap_namespace      = var.cloudmap_namespace
  image                   = local.ecr_image
  master_cpu              = var.regions["eu-west-1"].master_cpu
  master_memory           = var.regions["eu-west-1"].master_memory
  worker_cpu              = var.regions["eu-west-1"].worker_cpu
  worker_memory           = var.regions["eu-west-1"].worker_memory
  target_url              = var.target_url
  test_duration           = var.test_duration
  users                   = var.users
  connections             = var.connections
  rate                    = var.rate
  thresholds              = var.thresholds
  scenario_file           = var.scenario_file
  pywrkr_tags             = local.region_pywrkr_tags["eu-west-1"]
  otel_endpoint           = var.otel_endpoint
  prom_remote_write       = var.prom_remote_write
  log_retention_days      = var.log_retention_days
  tags                    = local.common_tags
}

# --- ap-southeast-1 ---

module "region_ap_southeast_1" {
  count  = try(var.regions["ap-southeast-1"].enabled, false) ? 1 : 0
  source = "./modules/regional"
  providers = {
    aws = aws.ap_southeast_1
  }

  name_prefix             = "${local.name_prefix}-apse1"
  region_name             = "ap-southeast-1"
  vpc_cidr                = var.regions["ap-southeast-1"].vpc_cidr
  az_count                = var.regions["ap-southeast-1"].az_count
  worker_count            = var.regions["ap-southeast-1"].worker_count
  egress_mode             = var.egress_mode
  enable_flow_logs        = var.enable_flow_logs
  flow_log_retention_days = var.flow_log_retention_days
  cloudmap_namespace      = var.cloudmap_namespace
  image                   = local.ecr_image
  master_cpu              = var.regions["ap-southeast-1"].master_cpu
  master_memory           = var.regions["ap-southeast-1"].master_memory
  worker_cpu              = var.regions["ap-southeast-1"].worker_cpu
  worker_memory           = var.regions["ap-southeast-1"].worker_memory
  target_url              = var.target_url
  test_duration           = var.test_duration
  users                   = var.users
  connections             = var.connections
  rate                    = var.rate
  thresholds              = var.thresholds
  scenario_file           = var.scenario_file
  pywrkr_tags             = local.region_pywrkr_tags["ap-southeast-1"]
  otel_endpoint           = var.otel_endpoint
  prom_remote_write       = var.prom_remote_write
  log_retention_days      = var.log_retention_days
  tags                    = local.common_tags
}
```

- [ ] **Step 2: Create outputs.tf**

```hcl
# -----------------------------------------------------------------------------
# Outputs — aggregated across all enabled regions
# -----------------------------------------------------------------------------

output "ecr_repository_url" {
  description = "ECR repository URL in the home region"
  value       = module.shared.ecr_repository_url
}

# --- Per-region outputs ---
# Each output is a map of region → value, only including enabled regions.

output "cluster_names" {
  description = "Map of region to ECS cluster name"
  value = merge(
    try(var.regions["us-east-1"].enabled, false) ? { "us-east-1" = module.region_us_east_1[0].cluster_name } : {},
    try(var.regions["eu-west-1"].enabled, false) ? { "eu-west-1" = module.region_eu_west_1[0].cluster_name } : {},
    try(var.regions["ap-southeast-1"].enabled, false) ? { "ap-southeast-1" = module.region_ap_southeast_1[0].cluster_name } : {},
  )
}

output "master_service_names" {
  description = "Map of region to master ECS service name"
  value = merge(
    try(var.regions["us-east-1"].enabled, false) ? { "us-east-1" = module.region_us_east_1[0].master_service_name } : {},
    try(var.regions["eu-west-1"].enabled, false) ? { "eu-west-1" = module.region_eu_west_1[0].master_service_name } : {},
    try(var.regions["ap-southeast-1"].enabled, false) ? { "ap-southeast-1" = module.region_ap_southeast_1[0].master_service_name } : {},
  )
}

output "master_dns_names" {
  description = "Map of region to master Cloud Map DNS name"
  value = merge(
    try(var.regions["us-east-1"].enabled, false) ? { "us-east-1" = module.region_us_east_1[0].master_dns_name } : {},
    try(var.regions["eu-west-1"].enabled, false) ? { "eu-west-1" = module.region_eu_west_1[0].master_dns_name } : {},
    try(var.regions["ap-southeast-1"].enabled, false) ? { "ap-southeast-1" = module.region_ap_southeast_1[0].master_dns_name } : {},
  )
}

output "worker_service_names" {
  description = "Map of region to list of worker ECS service names"
  value = merge(
    try(var.regions["us-east-1"].enabled, false) ? { "us-east-1" = module.region_us_east_1[0].worker_service_names } : {},
    try(var.regions["eu-west-1"].enabled, false) ? { "eu-west-1" = module.region_eu_west_1[0].worker_service_names } : {},
    try(var.regions["ap-southeast-1"].enabled, false) ? { "ap-southeast-1" = module.region_ap_southeast_1[0].worker_service_names } : {},
  )
}

output "master_log_group_names" {
  description = "Map of region to master CloudWatch log group name"
  value = merge(
    try(var.regions["us-east-1"].enabled, false) ? { "us-east-1" = module.region_us_east_1[0].master_log_group_name } : {},
    try(var.regions["eu-west-1"].enabled, false) ? { "eu-west-1" = module.region_eu_west_1[0].master_log_group_name } : {},
    try(var.regions["ap-southeast-1"].enabled, false) ? { "ap-southeast-1" = module.region_ap_southeast_1[0].master_log_group_name } : {},
  )
}

output "worker_log_group_names" {
  description = "Map of region to worker CloudWatch log group name"
  value = merge(
    try(var.regions["us-east-1"].enabled, false) ? { "us-east-1" = module.region_us_east_1[0].worker_log_group_name } : {},
    try(var.regions["eu-west-1"].enabled, false) ? { "eu-west-1" = module.region_eu_west_1[0].worker_log_group_name } : {},
    try(var.regions["ap-southeast-1"].enabled, false) ? { "ap-southeast-1" = module.region_ap_southeast_1[0].worker_log_group_name } : {},
  )
}

output "nat_eips" {
  description = "Map of region to list of NAT gateway Elastic IP addresses"
  value = merge(
    try(var.regions["us-east-1"].enabled, false) ? { "us-east-1" = module.region_us_east_1[0].nat_eips } : {},
    try(var.regions["eu-west-1"].enabled, false) ? { "eu-west-1" = module.region_eu_west_1[0].nat_eips } : {},
    try(var.regions["ap-southeast-1"].enabled, false) ? { "ap-southeast-1" = module.region_ap_southeast_1[0].nat_eips } : {},
  )
}

output "cloudmap_namespaces" {
  description = "Map of region to Cloud Map namespace name"
  value = merge(
    try(var.regions["us-east-1"].enabled, false) ? { "us-east-1" = module.region_us_east_1[0].namespace_name } : {},
    try(var.regions["eu-west-1"].enabled, false) ? { "eu-west-1" = module.region_eu_west_1[0].namespace_name } : {},
    try(var.regions["ap-southeast-1"].enabled, false) ? { "ap-southeast-1" = module.region_ap_southeast_1[0].namespace_name } : {},
  )
}
```

- [ ] **Step 3: Run terraform fmt on the entire project**

```bash
cd infra/multi-region/terraform
terraform fmt -recursive
```

- [ ] **Step 4: Run terraform validate**

```bash
cd infra/multi-region/terraform
terraform init -backend=false
terraform validate
```

Expected: `Success! The configuration is valid.`

If validation fails, fix the issues. Common problems: missing variable references, type mismatches, incorrect module output references.

- [ ] **Step 5: Commit**

```bash
git add infra/multi-region/terraform/main.tf infra/multi-region/terraform/outputs.tf
git commit -m "feat: add root Terraform configuration with multi-region module orchestration"
```

---

## Task 13: terraform.tfvars.example

**Files:**
- Create: `infra/multi-region/terraform/terraform.tfvars.example`

- [ ] **Step 1: Create terraform.tfvars.example**

```hcl
# =============================================================================
# Multi-Region pywrkr Load Test Configuration
# Copy to terraform.tfvars and customize.
# =============================================================================

# --- Global ---
project_name = "pywrkr"
environment  = "loadtest"
home_region  = "us-east-1"

# --- Regions ---
# Enable/disable regions and configure per-region resources.
# worker_count determines both the number of worker ECS services AND
# the number of NAT gateways/EIPs (in nat_eip mode) for source IP diversity.
regions = {
  "us-east-1" = {
    enabled       = true
    vpc_cidr      = "10.1.0.0/16"
    az_count      = 2
    worker_count  = 3
    master_cpu    = 1024
    master_memory = 2048
    worker_cpu    = 1024
    worker_memory = 2048
  }
  "eu-west-1" = {
    enabled       = true
    vpc_cidr      = "10.2.0.0/16"
    az_count      = 2
    worker_count  = 3
    master_cpu    = 1024
    master_memory = 2048
    worker_cpu    = 1024
    worker_memory = 2048
  }
  "ap-southeast-1" = {
    enabled       = true
    vpc_cidr      = "10.3.0.0/16"
    az_count      = 2
    worker_count  = 3
    master_cpu    = 1024
    master_memory = 2048
    worker_cpu    = 1024
    worker_memory = 2048
  }
}

# --- Networking ---
# "nat_eip"  = stable source IPs via per-worker NAT/EIP (more expensive, allowlistable)
# "public_ip" = ephemeral public IPs on Fargate tasks (cheaper, IPs not stable)
egress_mode    = "nat_eip"
enable_flow_logs = false

# --- Test configuration ---
target_url    = "https://example.com"
test_duration = 60    # seconds — passed to pywrkr as -d <float>
users         = 10
connections   = 10
rate          = 0        # 0 = unlimited
thresholds    = ["p95 < 500ms", "error_rate < 5%"]
scenario_file = ""       # e.g., "/scenarios/api-flow.json"

# --- Container image ---
image_tag = "latest"

# --- Observability (optional) ---
otel_endpoint     = ""
prom_remote_write = ""
log_retention_days = 7

# --- Safety ---
confirm_production     = "no"    # Set to "yes" for non-internal targets
max_duration_seconds   = 300
max_workers_per_region = 10

# --- Cloud Map ---
cloudmap_namespace = "pywrkr.local"

# --- Additional pywrkr tags ---
tags = {
  team = "platform"
  test = "website-benchmark"
}
```

- [ ] **Step 2: Commit**

```bash
git add infra/multi-region/terraform/terraform.tfvars.example
git commit -m "feat: add terraform.tfvars.example with annotated defaults"
```

---

## Task 14: Jenkinsfile

The most complex file — multi-region parallel pipeline with build, deploy, monitor, collect, and cleanup stages.

**Files:**
- Create: `infra/multi-region/jenkins/Jenkinsfile`

- [ ] **Step 1: Create Jenkinsfile**

Create `infra/multi-region/jenkins/Jenkinsfile`:

```groovy
// =============================================================================
// Multi-Region pywrkr Distributed Load Test Pipeline
//
// Builds pywrkr image, deploys to multiple AWS regions via Terraform,
// monitors test execution, collects results, and optionally cleans up.
// =============================================================================

pipeline {
    agent any

    options {
        timestamps()
        timeout(time: 120, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '20'))
        disableConcurrentBuilds()
    }

    parameters {
        string(name: 'AWS_REGIONS', defaultValue: 'us-east-1', description: 'Comma-separated AWS regions (e.g., us-east-1,eu-west-1,ap-southeast-1)')
        string(name: 'ENVIRONMENT', defaultValue: 'loadtest', description: 'Environment name')
        string(name: 'IMAGE_TAG', defaultValue: 'latest', description: 'Docker image tag')
        string(name: 'TARGET_URL', defaultValue: 'https://example.com', description: 'Target URL to benchmark')
        string(name: 'TEST_DURATION', defaultValue: '60', description: 'Test duration in seconds')
        string(name: 'USERS', defaultValue: '10', description: 'Virtual users per worker (0 = connection mode)')
        string(name: 'CONNECTIONS', defaultValue: '10', description: 'Concurrent connections per worker')
        string(name: 'RATE', defaultValue: '0', description: 'Req/sec rate limit per worker (0 = unlimited)')
        string(name: 'WORKER_COUNT_PER_REGION', defaultValue: '3', description: 'Number of workers per region')
        string(name: 'THRESHOLDS', defaultValue: 'p95 < 500ms,error_rate < 5%', description: 'Comma-separated thresholds')
        string(name: 'SCENARIO_FILE', defaultValue: '', description: 'Scenario file path in container (e.g., /scenarios/api-flow.json)')
        choice(name: 'EGRESS_MODE', choices: ['nat_eip', 'public_ip'], description: 'Egress: nat_eip (stable IPs) or public_ip (cheaper)')
        booleanParam(name: 'CLEANUP_AFTER_RUN', defaultValue: true, description: 'Destroy infrastructure after test')
        string(name: 'CONFIRM_PRODUCTION_TARGET', defaultValue: 'no', description: 'Must be "yes" for non-internal targets')
        string(name: 'OTEL_ENDPOINT', defaultValue: '', description: 'OpenTelemetry collector endpoint (optional)')
        string(name: 'PROM_REMOTE_WRITE', defaultValue: '', description: 'Prometheus pushgateway URL (optional)')
        string(name: 'MASTER_CPU', defaultValue: '1024', description: 'Master CPU units')
        string(name: 'MASTER_MEMORY', defaultValue: '2048', description: 'Master memory (MB)')
        string(name: 'WORKER_CPU', defaultValue: '1024', description: 'Worker CPU units')
        string(name: 'WORKER_MEMORY', defaultValue: '2048', description: 'Worker memory (MB)')
    }

    environment {
        TF_DIR        = "${WORKSPACE}/infra/multi-region/terraform"
        DOCKER_DIR    = "${WORKSPACE}/infra/multi-region"
        PROJECT_NAME  = 'pywrkr'
        HOME_REGION   = 'us-east-1'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Validate Parameters') {
            steps {
                script {
                    def regions = params.AWS_REGIONS.split(',').collect { it.trim() }
                    def workerCount = params.WORKER_COUNT_PER_REGION.toInteger()
                    def maxWorkers = 10

                    // Safety: production target confirmation
                    def targetUrl = params.TARGET_URL
                    def isInternal = targetUrl.contains('localhost') ||
                                     targetUrl.contains('127.0.0.1') ||
                                     targetUrl.contains('10.') ||
                                     targetUrl.contains('172.16.') ||
                                     targetUrl.contains('192.168.') ||
                                     targetUrl.contains('.internal') ||
                                     targetUrl.contains('example.com')

                    if (!isInternal && params.CONFIRM_PRODUCTION_TARGET != 'yes') {
                        error("Target URL '${targetUrl}' appears to be a production endpoint. Set CONFIRM_PRODUCTION_TARGET=yes to proceed.")
                    }

                    // Safety: max workers
                    if (workerCount > maxWorkers) {
                        error("WORKER_COUNT_PER_REGION (${workerCount}) exceeds maximum (${maxWorkers})")
                    }

                    // Safety: max duration
                    def durationSec = params.TEST_DURATION.toInteger()
                    if (durationSec > 300) {
                        error("TEST_DURATION (${durationSec}s) exceeds maximum (300s). Increase max_duration_seconds if needed.")
                    }

                    env.REGIONS_LIST = params.AWS_REGIONS
                    env.REGION_COUNT = regions.size().toString()
                    echo "Validated: ${regions.size()} region(s), ${workerCount} workers/region, duration=${durationStr}, egress=${params.EGRESS_MODE}"
                }
            }
        }

        stage('Build Docker Image') {
            steps {
                script {
                    sh "docker build -f ${DOCKER_DIR}/Dockerfile -t ${PROJECT_NAME}:${params.IMAGE_TAG} ."
                }
            }
        }

        stage('Push to ECR') {
            steps {
                script {
                    def accountId = sh(script: 'aws sts get-caller-identity --query Account --output text', returnStdout: true).trim()
                    def ecrUrl = "${accountId}.dkr.ecr.${HOME_REGION}.amazonaws.com"
                    def repoName = "${PROJECT_NAME}-${params.ENVIRONMENT}-pywrkr"
                    def fullImage = "${ecrUrl}/${repoName}:${params.IMAGE_TAG}"

                    sh """
                        aws ecr get-login-password --region ${HOME_REGION} | docker login --username AWS --password-stdin ${ecrUrl}
                        docker tag ${PROJECT_NAME}:${params.IMAGE_TAG} ${fullImage}
                        docker push ${fullImage}
                    """

                    env.ECR_IMAGE = fullImage
                    env.ECR_URL = ecrUrl
                    env.ECR_REPO_NAME = repoName
                    env.AWS_ACCOUNT_ID = accountId
                }
            }
        }

        stage('Wait for ECR Replication') {
            steps {
                script {
                    def regions = params.AWS_REGIONS.split(',').collect { it.trim() }
                    def targetRegions = regions.findAll { it != HOME_REGION }

                    if (targetRegions.isEmpty()) {
                        echo "Single region — no replication needed"
                        return
                    }

                    echo "Waiting for ECR replication to: ${targetRegions.join(', ')}"

                    for (region in targetRegions) {
                        def found = false
                        for (int i = 0; i < 12; i++) {
                            def result = sh(
                                script: "aws ecr describe-images --repository-name ${env.ECR_REPO_NAME} --image-ids imageTag=${params.IMAGE_TAG} --region ${region} 2>/dev/null || echo 'NOT_FOUND'",
                                returnStdout: true
                            ).trim()

                            if (!result.contains('NOT_FOUND') && !result.contains('ImageNotFoundException')) {
                                echo "Image replicated to ${region}"
                                found = true
                                break
                            }
                            echo "Waiting for replication to ${region}... (attempt ${i + 1}/12)"
                            sleep(5)
                        }
                        if (!found) {
                            error("ECR replication to ${region} timed out after 60s")
                        }
                    }
                }
            }
        }

        stage('Terraform Init & Apply') {
            steps {
                script {
                    def regions = params.AWS_REGIONS.split(',').collect { it.trim() }
                    def workerCount = params.WORKER_COUNT_PER_REGION.toInteger()
                    def thresholdList = params.THRESHOLDS ? params.THRESHOLDS.split(',').collect { "\"${it.trim()}\"" }.join(', ') : ''

                    // Generate terraform.auto.tfvars
                    def regionsBlock = regions.collect { region ->
                        def cidrIndex = ['us-east-1': 1, 'eu-west-1': 2, 'ap-southeast-1': 3]
                        def idx = cidrIndex.getOrDefault(region, regions.indexOf(region) + 1)
                        """  "${region}" = {
    enabled       = true
    vpc_cidr      = "10.${idx}.0.0/16"
    az_count      = 2
    worker_count  = ${workerCount}
    master_cpu    = ${params.MASTER_CPU}
    master_memory = ${params.MASTER_MEMORY}
    worker_cpu    = ${params.WORKER_CPU}
    worker_memory = ${params.WORKER_MEMORY}
  }"""
                    }.join('\n')

                    def tfvars = """
project_name       = "${PROJECT_NAME}"
environment        = "${params.ENVIRONMENT}"
home_region        = "${HOME_REGION}"
egress_mode        = "${params.EGRESS_MODE}"
target_url         = "${params.TARGET_URL}"
test_duration      = ${params.TEST_DURATION}
users              = ${params.USERS}
connections        = ${params.CONNECTIONS}
rate               = ${params.RATE}
thresholds         = [${thresholdList}]
scenario_file      = "${params.SCENARIO_FILE}"
image_tag          = "${params.IMAGE_TAG}"
otel_endpoint      = "${params.OTEL_ENDPOINT}"
prom_remote_write  = "${params.PROM_REMOTE_WRITE}"
confirm_production = "${params.CONFIRM_PRODUCTION_TARGET}"

regions = {
${regionsBlock}
}

tags = {
  jenkins_build = "${env.BUILD_NUMBER}"
  jenkins_job   = "${env.JOB_NAME}"
}
"""
                    writeFile file: "${TF_DIR}/jenkins.auto.tfvars", text: tfvars

                    dir(TF_DIR) {
                        sh 'terraform init'
                        sh 'terraform plan -out=tfplan'
                        sh 'terraform apply tfplan'
                    }
                }
            }
        }

        stage('Run Load Tests') {
            steps {
                script {
                    def regions = params.AWS_REGIONS.split(',').collect { it.trim() }
                    def parallelStages = [:]

                    for (region in regions) {
                        def r = region
                        parallelStages["Test: ${r}"] = {
                            runRegionalTest(r)
                        }
                    }

                    parallel parallelStages
                }
            }
        }

        stage('Aggregate Results') {
            steps {
                script {
                    def regions = params.AWS_REGIONS.split(',').collect { it.trim() }
                    def summary = [regions: [:], overall_pass: true]

                    for (region in regions) {
                        def resultFile = "results/${region}/results.json"
                        if (fileExists(resultFile)) {
                            def content = readFile(resultFile)
                            summary.regions[region] = [
                                results: content,
                                status: 'collected'
                            ]
                        } else {
                            summary.regions[region] = [status: 'missing']
                            summary.overall_pass = false
                        }
                    }

                    // Check for threshold breaches
                    for (region in regions) {
                        def exitCodeFile = "results/${region}/exit_code"
                        if (fileExists(exitCodeFile)) {
                            def exitCode = readFile(exitCodeFile).trim().toInteger()
                            if (exitCode == 2) {
                                echo "THRESHOLD BREACH in ${region}"
                                summary.overall_pass = false
                            } else if (exitCode != 0) {
                                echo "ERROR in ${region} (exit code ${exitCode})"
                                summary.overall_pass = false
                            }
                        }
                    }

                    writeFile file: 'results/summary.json', text: groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson(summary))

                    if (!summary.overall_pass) {
                        unstable('One or more regions had threshold breaches or errors')
                    }
                }
            }
        }

        stage('Archive Artifacts') {
            steps {
                archiveArtifacts artifacts: 'results/**/*', allowEmptyArchive: true
            }
        }
    }

    post {
        always {
            script {
                if (params.CLEANUP_AFTER_RUN) {
                    echo 'Cleaning up infrastructure (CLEANUP_AFTER_RUN=true)...'
                    dir(TF_DIR) {
                        sh 'terraform destroy -auto-approve || true'
                    }
                } else {
                    echo 'Skipping cleanup (CLEANUP_AFTER_RUN=false). Infrastructure remains running.'
                    echo 'To destroy manually: cd infra/multi-region/terraform && terraform destroy'
                }
            }
        }
        failure {
            echo 'Pipeline failed. Check logs for details.'
        }
        success {
            echo 'Load test completed successfully.'
        }
    }
}

// =============================================================================
// Helper: Run load test in a single region
// =============================================================================

def runRegionalTest(String region) {
    def tfDir = "${WORKSPACE}/infra/multi-region/terraform"

    // Get Terraform outputs for this region
    def clusterName = sh(
        script: "cd ${tfDir} && terraform output -json cluster_names | python3 -c \"import sys,json; print(json.load(sys.stdin).get('${region}',''))\"",
        returnStdout: true
    ).trim()

    def masterService = sh(
        script: "cd ${tfDir} && terraform output -json master_service_names | python3 -c \"import sys,json; print(json.load(sys.stdin).get('${region}',''))\"",
        returnStdout: true
    ).trim()

    def masterLogGroup = sh(
        script: "cd ${tfDir} && terraform output -json master_log_group_names | python3 -c \"import sys,json; print(json.load(sys.stdin).get('${region}',''))\"",
        returnStdout: true
    ).trim()

    def workerServices = sh(
        script: "cd ${tfDir} && terraform output -json worker_service_names | python3 -c \"import sys,json; print(','.join(json.load(sys.stdin).get('${region}',[])))\"",
        returnStdout: true
    ).trim()

    if (!clusterName || !masterService) {
        error("Could not get Terraform outputs for region ${region}")
    }

    echo "[${region}] Cluster: ${clusterName}, Master: ${masterService}"

    // Force new deployment to pick up latest config
    sh "aws ecs update-service --cluster ${clusterName} --service ${masterService} --force-new-deployment --region ${region}"
    for (svc in workerServices.split(',')) {
        if (svc) {
            sh "aws ecs update-service --cluster ${clusterName} --service ${svc} --force-new-deployment --region ${region}"
        }
    }

    // Wait for services to stabilize
    echo "[${region}] Waiting for master service to stabilize..."
    sh "aws ecs wait services-stable --cluster ${clusterName} --services ${masterService} --region ${region}"
    echo "[${region}] Master stable."

    // Wait for test to complete (duration + buffer for startup/collection)
    def durationSec = params.TEST_DURATION.toInteger()
    def waitSec = durationSec + 120 // buffer for worker connection + result collection
    echo "[${region}] Waiting ${waitSec}s for test completion..."
    sleep(waitSec)

    // Collect results from CloudWatch logs
    sh "mkdir -p results/${region}"

    def endTime = System.currentTimeMillis()
    def startTime = endTime - ((durationSec + 300) * 1000)

    // Poll for results marker in logs
    def found = false
    for (int attempt = 0; attempt < 30; attempt++) {
        def logOutput = sh(
            script: """
                aws logs filter-log-events \
                    --log-group-name '${masterLogGroup}' \
                    --start-time ${startTime} \
                    --end-time ${endTime} \
                    --filter-pattern 'PYWRKR_JSON_END' \
                    --region ${region} \
                    --output text 2>/dev/null || echo ''
            """,
            returnStdout: true
        ).trim()

        if (logOutput.contains('PYWRKR_JSON_END')) {
            found = true
            break
        }
        echo "[${region}] Waiting for results... (attempt ${attempt + 1}/30)"
        sleep(10)
    }

    if (!found) {
        echo "[${region}] WARNING: Could not find result markers in logs"
        writeFile file: "results/${region}/exit_code", text: '1'
        return
    }

    // Extract JSON results
    sh """
        aws logs filter-log-events \
            --log-group-name '${masterLogGroup}' \
            --start-time ${startTime} \
            --end-time ${System.currentTimeMillis()} \
            --region ${region} \
            --output json > results/${region}/raw_logs.json
    """

    sh """
        python3 -c "
import json, re, sys
with open('results/${region}/raw_logs.json') as f:
    data = json.load(f)
messages = ' '.join(e.get('message','') for e in data.get('events',[]))
m = re.search(r'---PYWRKR_JSON_START---(.+?)---PYWRKR_JSON_END---', messages, re.DOTALL)
if m:
    result = json.loads(m.group(1).strip())
    with open('results/${region}/results.json', 'w') as out:
        json.dump(result, out, indent=2)
    # Check for threshold breaches (pywrkr exit code 2)
    sys.exit(0)
else:
    print('No JSON results found in logs')
    sys.exit(1)
" || echo '1' > results/${region}/exit_code
    """

    // Capture exit code from the ECS task
    def taskArns = sh(
        script: "aws ecs list-tasks --cluster ${clusterName} --service-name ${masterService} --desired-status STOPPED --region ${region} --query 'taskArns[0]' --output text 2>/dev/null || echo ''",
        returnStdout: true
    ).trim()

    if (taskArns && taskArns != 'None') {
        def exitCode = sh(
            script: "aws ecs describe-tasks --cluster ${clusterName} --tasks ${taskArns} --region ${region} --query 'tasks[0].containers[0].exitCode' --output text 2>/dev/null || echo '0'",
            returnStdout: true
        ).trim()
        writeFile file: "results/${region}/exit_code", text: exitCode
        echo "[${region}] Master exit code: ${exitCode}"
    } else {
        writeFile file: "results/${region}/exit_code", text: '0'
    }
}
```

- [ ] **Step 2: Verify Jenkinsfile syntax**

The Jenkinsfile is Groovy-based — no automated lint available in most setups. Visually review for:
- Balanced braces and parentheses
- Correct string interpolation (double quotes for GString)
- No Jenkins Pipeline syntax errors

- [ ] **Step 3: Commit**

```bash
git add infra/multi-region/jenkins/Jenkinsfile
git commit -m "feat: add multi-region Jenkins pipeline for distributed load testing"
```

---

## Task 15: README.md

**Files:**
- Create: `infra/multi-region/README.md`

- [ ] **Step 1: Create README.md**

Create `infra/multi-region/README.md` with comprehensive deployment and operating instructions. The README should cover:

1. **Overview** — what this deploys and why
2. **Architecture diagram** (ASCII) showing regional test cells
3. **Prerequisites** — AWS CLI, Terraform, Docker, Jenkins, required IAM permissions, AWS quota requirements (EIPs, NATs, Fargate tasks)
4. **Quick Start** — minimal steps to run a single-region test
5. **Multi-Region Deployment** — step-by-step with Terraform
6. **Jenkins Pipeline** — how to configure and run
7. **Egress Modes** — NAT/EIP vs public IP with cost comparison table
8. **Source IP Control** — how per-subnet NAT pinning works, how to get EIPs for allowlisting
9. **Scenario Modes** — simple URL, scenario file, HAR import
10. **Observability** — OTel, Prometheus, CloudWatch
11. **Safety Controls** — production confirmation, duration/worker caps, cleanup
12. **Cost Estimates** — table from spec (NAT/EIP vs public IP for 3 regions)
13. **Adding New Regions** — add provider alias + module block
14. **Cleanup** — `terraform destroy` releases all EIPs and resources
15. **Troubleshooting** — common issues (ECR replication timeout, service instability, log collection)
16. **Why ECS/Fargate** — brief explanation vs EKS
17. **Future Improvements** — EKS/Karpenter, Step Functions, centralized observability, Fargate Spot

The README should be written in a direct, operational style focused on how-to steps. Refer to the spec document for design rationale.

Content is intentionally not included verbatim in this plan to keep the plan focused on code deliverables. The implementer should use the spec at `docs/superpowers/specs/2026-03-18-multi-region-pywrkr-design.md` as the source of truth for all facts, figures, and design decisions.

- [ ] **Step 2: Commit**

```bash
git add infra/multi-region/README.md
git commit -m "docs: add multi-region deployment and operating guide"
```

---

## Task 16: Final Validation

- [ ] **Step 1: Run terraform fmt on everything**

```bash
cd infra/multi-region/terraform
terraform fmt -recursive -check
```

- [ ] **Step 2: Run terraform validate**

```bash
cd infra/multi-region/terraform
terraform init -backend=false
terraform validate
```

Expected: `Success! The configuration is valid.`

- [ ] **Step 3: Verify file tree matches spec**

```bash
find infra/multi-region -type f | sort
```

Verify all 35+ files are present and match the spec's file structure.

- [ ] **Step 4: Verify Dockerfile builds**

```bash
docker build -f infra/multi-region/Dockerfile -t pywrkr:multi-region-test .
```

- [ ] **Step 5: Verify existing infra is untouched**

```bash
git diff infra/terraform/
git diff infra/jenkins/
git diff infra/scenarios/
```

Expected: No changes to any existing infra files.

- [ ] **Step 6: Create final commit if any formatting fixes were needed**

```bash
git add -A infra/multi-region/
git commit -m "chore: final formatting and validation fixes"
```
