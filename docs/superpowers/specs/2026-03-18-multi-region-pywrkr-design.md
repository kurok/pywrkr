# Multi-Region Distributed pywrkr on AWS ECS/Fargate

**Date:** 2026-03-18
**Status:** Draft
**Location:** `infra/multi-region/`

## 1. Problem Statement

pywrkr supports distributed master/worker load testing, but lacks production-ready infrastructure for running multi-region tests with controlled source-IP diversity on AWS. The existing `infra/` provides a single-region setup. This design adds a comprehensive multi-region solution alongside it.

## 2. Goals

- Deploy distributed pywrkr load tests across multiple AWS regions simultaneously
- Control outbound source IPs via per-subnet NAT gateway/EIP routing
- Provide a turnkey Jenkins pipeline that builds, deploys, runs, collects results, and cleans up
- Minimize operational burden using ECS Fargate (no cluster management)
- Keep the existing simpler `infra/` setup untouched

## 3. Non-Goals

- Cross-region master/worker communication (each region is independent)
- Long-running always-on infrastructure (deploy per test, destroy after)
- EKS/Kubernetes (Fargate is sufficient for this workload)
- Custom metrics pipeline (CloudWatch + optional OTel/Prometheus export covers needs)

## 4. Architecture Overview

### 4.1 Deployment Topology

```
Jenkins Controller
  ├── Build & push image to ECR (home region)
  ├── ECR replicates to target regions
  └── Terraform apply (single root module)
        ├── Shared layer: ECR repo + replication rules
        └── Static module blocks per region (count-gated by enabled flag):
              └── Regional Test Cell
                    ├── VPC (2+ AZs)
                    ├── Public subnets (NAT gateways or public-IP tasks)
                    ├── Private worker subnets (1 per source IP)
                    ├── Private master subnet
                    ├── ECS Fargate cluster
                    ├── Cloud Map namespace (pywrkr.local)
                    ├── Master ECS service → pywrkr-master.pywrkr.local
                    ├── Worker ECS services (1 per subnet, pinned)
                    ├── CloudWatch log groups
                    ├── IAM roles
                    └── Security groups
```

### 4.2 Regional Test Cell Detail

Each region contains:

| Component | Count | Purpose |
|-----------|-------|---------|
| VPC | 1 | Network isolation |
| AZs | 2+ | Availability |
| Public subnets | 1 per AZ | NAT gateway placement or public-IP tasks |
| Private worker subnets | 1 per desired source IP (default 3) | IP-pinned worker egress |
| Private master subnet | 1 | Master task networking |
| NAT gateways | 1 per worker subnet (NAT mode) | Outbound routing |
| Elastic IPs | 1 per NAT gateway | Stable source IPs |
| ECS cluster | 1 | Fargate task hosting |
| Cloud Map namespace | 1 (`pywrkr.local`) | Service discovery |
| Master service | 1 | pywrkr master process |
| Worker services | N (default 3) | pywrkr worker processes |
| CloudWatch log groups | 1 per service | Log aggregation |
| Security groups | 2 (master, worker) | Network access control |
| IAM roles | 2 (execution, task) | AWS API permissions |

### 4.3 Multi-Source-IP Egress (NAT/EIP Mode — Default)

```
worker-a service → private-subnet-a → route-table-a → nat-gw-a → eip-a (54.x.x.1)
worker-b service → private-subnet-b → route-table-b → nat-gw-b → eip-b (54.x.x.2)
worker-c service → private-subnet-c → route-table-c → nat-gw-c → eip-c (54.x.x.3)
```

Each worker ECS service is pinned to exactly one private subnet via `network_configuration.subnets`. That subnet's route table sends `0.0.0.0/0` to a dedicated NAT gateway with its own EIP. This gives each worker a deterministic, allowlistable outbound IP.

### 4.4 Public-IP Mode (Alternative)

Workers placed in public subnets with `assign_public_ip = true`. Each Fargate task gets an ephemeral public IP from AWS's pool. No NAT gateways are created.

**Tradeoffs:**
- Cheaper (no NAT gateway hourly cost or data processing charges)
- More source IP diversity (each task gets a different IP)
- IPs are not stable or predictable — cannot be allowlisted

### 4.5 Service Discovery

- Cloud Map private DNS namespace: `pywrkr.local`
- Master registers as: `pywrkr-master.pywrkr.local`
- Workers connect to master via: `pywrkr-master.pywrkr.local:9220`
- Cloud Map SRV/A records resolve within the VPC

### 4.6 Image Distribution

- ECR repository created in home region (first region in the list)
- ECR replication rules push the image to all other target regions
- Each region's ECS tasks pull from their local ECR endpoint
- Avoids cross-region image pulls and cold start latency

## 5. File Structure

```
infra/multi-region/
├── Dockerfile                          # pywrkr image with [otel] extra
├── README.md                           # Deployment and operating instructions
├── terraform/
│   ├── main.tf                         # Root: shared layer + static per-region module blocks (count-gated)
│   ├── variables.tf                    # All input parameters
│   ├── outputs.tf                      # Per-region outputs
│   ├── locals.tf                       # Derived values, region config
│   ├── versions.tf                     # Terraform + provider versions
│   ├── providers.tf                    # Provider aliases per region
│   ├── backend.tf                      # Local default, S3 example commented
│   ├── terraform.tfvars.example        # Example configuration
│   └── modules/
│       ├── regional/
│       │   ├── main.tf                 # Composes all per-region modules (network, iam, ecs, etc.)
│       │   ├── variables.tf
│       │   └── outputs.tf
│       ├── shared/
│       │   ├── main.tf                 # ECR repo + replication rules
│       │   ├── variables.tf
│       │   └── outputs.tf
│       ├── network/
│       │   ├── main.tf                 # VPC, subnets, NATs, EIPs, route tables, SGs
│       │   ├── variables.tf
│       │   └── outputs.tf
│       ├── iam/
│       │   ├── main.tf                 # Task execution role, task role
│       │   ├── variables.tf
│       │   └── outputs.tf
│       ├── ecs-cluster/
│       │   ├── main.tf                 # ECS cluster + Cloud Map namespace
│       │   ├── variables.tf
│       │   └── outputs.tf
│       ├── logging/
│       │   ├── main.tf                 # CloudWatch log groups
│       │   ├── variables.tf
│       │   └── outputs.tf
│       ├── pywrkr-master/
│       │   ├── main.tf                 # Master task def + service + Cloud Map registration
│       │   ├── variables.tf
│       │   └── outputs.tf
│       ├── pywrkr-worker/
│       │   ├── main.tf                 # Worker task def + per-subnet services
│       │   ├── variables.tf
│       │   └── outputs.tf
│       └── observability/
│           ├── main.tf                 # VPC flow logs, optional CW dashboard
│           ├── variables.tf
│           └── outputs.tf
├── jenkins/
│   └── Jenkinsfile                     # Declarative pipeline with parallel regions
└── scenarios/
    ├── simple-get.json                 # Basic URL test
    ├── api-flow.json                   # Multi-step API scenario
    └── har-example.json                # HAR-imported scenario
```

## 6. Terraform Design

### 6.1 State Topology

Single root module with:
- **Shared layer:** ECR repository + replication config (created once in home region)
- **Regional layer:** Static module blocks per supported region, each gated by `count = var.regions["<region>"].enabled ? 1 : 0`

Default backend: local state. Commented S3+DynamoDB example included in `backend.tf`.

**Why not `for_each`?** Terraform does not allow dynamic provider references inside `for_each` module blocks. The `providers` meta-argument requires static provider alias references. Therefore, each supported region gets its own static module block in `main.tf`, enabled/disabled via the `count` meta-argument based on the `var.regions` map.

### 6.2 Provider Strategy

```hcl
# providers.tf — static aliases for all supported regions
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}
provider "aws" {
  alias  = "eu_west_1"
  region = "eu-west-1"
}
provider "aws" {
  alias  = "ap_southeast_1"
  region = "ap-southeast-1"
}

# main.tf — static module blocks, count-gated
module "region_us_east_1" {
  count     = try(var.regions["us-east-1"].enabled, false) ? 1 : 0
  source    = "./modules/regional"
  providers = { aws = aws.us_east_1 }
  # ... region config from var.regions["us-east-1"]
}
# (repeated for each supported region)
```

To add a new region: add a provider alias in `providers.tf` and a module block in `main.tf`. This is intentionally verbose to stay within Terraform's static provider constraints.

**Note on existing infra:** The existing `infra/terraform/` uses port 9000 in security groups and commands, while the actual pywrkr default is 9220 (`DEFAULT_MASTER_PORT` in `config.py`). This multi-region design uses the correct default port 9220 throughout.

### 6.3 Module Boundaries

| Module | Inputs | Outputs | Responsibility |
|--------|--------|---------|---------------|
| `regional` | region_name, region_config, ecr_repo_url, environment, egress_mode, target_url, test config, image_tag, tags | all per-region outputs (cluster, services, logs, eips, namespace) | Composition module — wires together all per-region modules. Called once per region from root `main.tf` with count gate. |
| `shared` | home_region, target_regions, ecr_repo_name | ecr_repo_url, ecr_repo_arns | ECR + replication |
| `network` | vpc_cidr, az_count, worker_subnet_count, egress_mode, tags | vpc_id, private_subnet_ids, public_subnet_ids, master_subnet_id, worker_subnet_ids (flat list, indexed by worker index), nat_eips, master_sg_id, worker_sg_id | Full networking |
| `iam` | environment, tags | execution_role_arn, task_role_arn | IAM roles + policies |
| `ecs-cluster` | cluster_name, namespace_name, vpc_id, tags | cluster_id, cluster_arn, namespace_id, namespace_arn | ECS + Cloud Map |
| `logging` | environment, region, retention_days, tags | master_log_group, worker_log_group_prefix | CW log groups |
| `pywrkr-master` | cluster_id, namespace_id, task config, network config, log group, image, tags | service_name, service_arn, dns_name | Master ECS service |
| `pywrkr-worker` | cluster_id, task config, network config per subnet, log group prefix, image, master_dns, worker_count, tags | service_names, service_arns | N worker services |
| `observability` | vpc_id, enable_flow_logs, flow_log_retention_days, tags | flow_log_id, flow_log_group_name | VPC flow logs to CloudWatch Logs (enabled via `enable_flow_logs` bool; CW dashboard is out of scope for initial implementation) |

### 6.4 Key Variables

```hcl
variable "regions" {
  description = "Map of region name to region-specific config"
  type = map(object({
    enabled             = bool
    vpc_cidr            = string
    az_count            = optional(number, 2)
    worker_count        = optional(number, 3)
    master_cpu          = optional(number, 1024)
    master_memory       = optional(number, 2048)
    worker_cpu          = optional(number, 1024)
    worker_memory       = optional(number, 2048)
  }))
}

variable "environment"          {}  # e.g., "loadtest"
variable "egress_mode"          {}  # "nat_eip" or "public_ip"
variable "target_url"           {}  # URL under test
variable "test_duration"        {}  # string, e.g., "60s" — passed directly to pywrkr CLI -d flag
variable "users"                {}  # virtual users per worker
variable "connections"          {}  # concurrent connections per worker (default 10)
variable "rate"                 {}  # requests/sec per worker
variable "thresholds"           {}  # e.g., "p95 < 500ms,error_rate < 5%"
variable "scenario_file"        {}  # path to scenario JSON (optional)
variable "image_tag"            {}  # Docker image tag
variable "otel_endpoint"        {}  # optional OTel collector URL
variable "prom_remote_write"    {}  # optional Prometheus pushgateway URL (matches pywrkr CLI flag name)
variable "enable_flow_logs"     {}  # bool
# Note: VPC CIDRs in var.regions must not overlap if VPC peering is ever added.
# The implementation should include a validation rule to catch this.
variable "confirm_production"   {}  # must be "yes" for non-internal targets
variable "max_duration_seconds" {}  # safety cap, default 300
variable "max_workers_per_region" {} # safety cap, default 10
```

### 6.5 Key Outputs

```hcl
output "ecr_repo_url"           {}  # ECR repo URL in home region
output "cluster_names"          {}  # map: region → cluster name
output "master_dns_names"       {}  # map: region → pywrkr-master.pywrkr.local
output "master_service_names"   {}  # map: region → master service name
output "worker_service_names"   {}  # map: region → list of worker service names
output "log_group_names"        {}  # map: region → {master, workers[]}
output "nat_eips"               {}  # map: region → list of EIP addresses
output "cloudmap_namespaces"    {}  # map: region → namespace name
```

### 6.6 EIP Lifecycle

EIPs are created by the `network` module as standard `aws_eip` resources with no `lifecycle.prevent_destroy`. On `terraform destroy`:
1. NAT gateways are destroyed (detaches EIP)
2. EIPs are released back to AWS
3. All associated costs stop immediately

Resources are tagged with `project=pywrkr` and `environment` for orphan detection if a destroy fails partway.

## 7. Networking & Security

### 7.1 Security Groups

**Master SG:**
- Inbound: TCP 9220 from worker SG (security group reference, not CIDR)
- Outbound: HTTP (80) + HTTPS (443) to `0.0.0.0/0` (target may use HTTP; OTel/Prometheus export uses HTTPS)
- Outbound: DNS (UDP 53, TCP 53) to VPC CIDR (required for Cloud Map resolution)

**Worker SG:**
- Inbound: none
- Outbound: TCP 9220 to master SG (security group reference for least-privilege)
- Outbound: HTTP (80) + HTTPS (443) to `0.0.0.0/0` (target endpoints + observability)
- Outbound: DNS (UDP 53, TCP 53) to VPC CIDR (required for Cloud Map resolution of master DNS)

This is intentionally tighter than the existing `infra/` which uses `0.0.0.0/0` for all worker egress.

### 7.2 Network ACLs

Default VPC NACLs (allow all). Security groups provide the primary access control.

### 7.3 VPC Endpoints (Optional)

- `com.amazonaws.<region>.ecr.api` — ECR API
- `com.amazonaws.<region>.ecr.dkr` — ECR Docker
- `com.amazonaws.<region>.s3` — S3 gateway (ECR layers)
- `com.amazonaws.<region>.logs` — CloudWatch Logs

These reduce NAT data transfer costs for AWS API traffic. Not required for basic operation.

## 8. ECS Design

### 8.1 Task Definitions

**Master:**
```json
{
  "family": "pywrkr-master",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "1024",
  "memory": "2048",
  "containerDefinitions": [{
    "name": "pywrkr-master",
    "image": "<ecr_url>:<tag>",
    "command": ["--master", "--port", "9220", "--expect-workers", "N",
                "--duration", "60s", "--threshold", "p95 < 500ms",
                "<target_url>"],
    "portMappings": [{"containerPort": 9220, "protocol": "tcp"}],
    "logConfiguration": {"logDriver": "awslogs", ...},
    "healthCheck": {
      "command": ["CMD-SHELL", "python -c 'import socket; s=socket.socket(); s.settimeout(2); s.connect((\"127.0.0.1\", 9220)); s.close()'"],
      "interval": 10,
      "timeout": 5,
      "retries": 3,
      "startPeriod": 30
    }
  }]
}
```

**Worker:**
```json
{
  "family": "pywrkr-worker",
  "command": ["--worker", "pywrkr-master.pywrkr.local:9220"],
  ...
}
```

### 8.2 Service Configuration

- Master: `desired_count = 1`, registered with Cloud Map
- Workers: `desired_count = 1` per service, N services total, each pinned to one subnet
- `deployment_minimum_healthy_percent = 0` (test workloads, not production services)
- `deployment_maximum_percent = 100`
- Services can be scaled to zero after tests via Jenkins cleanup stage

### 8.3 Run Modes

1. **Simple URL mode:** `command = ["--master", "--port", "9220", "--expect-workers", "3", "-d", "60s", "-u", "10", "https://example.com"]`
2. **Scenario mode:** Scenario file baked into image or mounted. `command = ["--master", "--port", "9220", "--expect-workers", "3", "--scenario", "/scenarios/api-flow.json"]`
3. **HAR mode:** HAR file pre-converted to scenario JSON, then used as scenario mode.

## 9. Jenkins Pipeline

### 9.1 Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `AWS_REGIONS` | string | `us-east-1` | Comma-separated regions |
| `ENVIRONMENT` | string | `loadtest` | Environment name |
| `IMAGE_TAG` | string | `latest` | Docker image tag |
| `TARGET_URL` | string | (required) | URL to test |
| `TEST_DURATION` | string | `60s` | Test duration |
| `USERS` | string | `10` | Virtual users per worker |
| `RATE` | string | `0` | Requests/sec (0 = unlimited) |
| `WORKER_COUNT_PER_REGION` | string | `3` | Workers per region |
| `THRESHOLDS` | string | `p95 < 500ms,error_rate < 5%` | Comma-separated thresholds |
| `SCENARIO_FILE` | string | (empty) | Path to scenario JSON |
| `EGRESS_MODE` | choice | `nat_eip` | `nat_eip` or `public_ip` |
| `CLEANUP_AFTER_RUN` | boolean | `true` | Destroy infra after test |
| `CONFIRM_PRODUCTION_TARGET` | string | `no` | Must be `yes` for prod URLs |
| `OTEL_ENDPOINT` | string | (empty) | Optional OTel collector URL |
| `CONNECTIONS` | string | `10` | Concurrent connections per worker |
| `PROM_REMOTE_WRITE` | string | (empty) | Optional Prometheus pushgateway |
| `MASTER_CPU` | string | `1024` | Master CPU units |
| `MASTER_MEMORY` | string | `2048` | Master memory (MB) |
| `WORKER_CPU` | string | `1024` | Worker CPU units |
| `WORKER_MEMORY` | string | `2048` | Worker memory (MB) |

### 9.2 Pipeline Stages

```
1. Checkout
2. Validate Parameters (safety checks: confirm_production, max duration, max workers)
3. Build Docker Image
4. Push to ECR (home region)
5. Wait for ECR Replication (poll `aws ecr describe-images` in each target region until image tag appears, 60s timeout)
6. Terraform Init + Plan + Apply
7. Parallel per region:
   a. Wait for master service stability
   b. Wait for worker services stability
   c. Wait for test duration + buffer
   d. Fetch CloudWatch logs
   e. Extract results from master logs
8. Aggregate Results (combine per-region results into summary)
9. Evaluate Thresholds (fail build if any region breached)
10. Archive Artifacts (logs, results JSON, summary)
11. Cleanup (if enabled: terraform destroy → releases all EIPs, NATs, VPCs)
```

### 9.3 Result Aggregation

Jenkins parses master container logs for each region to extract:
- Request counts, error counts, latency percentiles
- Threshold pass/fail results
- Per-region summary + combined summary

Archived as `results/<region>/output.json` and `results/summary.json`.

## 10. Dockerfile (infra/multi-region/)

```dockerfile
FROM python:3.13-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
RUN pip install --no-cache-dir build && python -m build --wheel

FROM python:3.13-slim
COPY --from=builder /build/dist/*.whl /tmp/
# pip handles the glob internally; [otel] is a PEP 508 extra specifier, not shell globbing
RUN pip install --no-cache-dir "/tmp/*.whl[otel]" && rm /tmp/*.whl
COPY infra/multi-region/scenarios/ /scenarios/
ENTRYPOINT ["pywrkr"]
```

Key differences from root Dockerfile:
- Installs `[otel]` extra for OpenTelemetry/Prometheus observability support. Deliberately excludes `[tui]`, `[dev]`, `[lint]`, `[security]` extras which are not needed in a production load-test image.
- Copies scenario files into `/scenarios/` for scenario mode

## 11. Safety Controls

| Control | Implementation |
|---------|---------------|
| Production target confirmation | `confirm_production` variable must be `"yes"` for non-RFC1918/localhost URLs |
| Max duration | `max_duration_seconds` default 300, validated in Jenkins and Terraform |
| Max workers | `max_workers_per_region` default 10, validated in Terraform |
| Resource tagging | All resources tagged: `project=pywrkr`, `environment`, `managed-by=terraform` |
| Cleanup default | `CLEANUP_AFTER_RUN=true` — infra torn down after every test by default |
| EIP release | EIPs have no `prevent_destroy` — `terraform destroy` releases them |
| Orphan detection | Tags enable `aws ec2 describe-addresses --filters Name=tag:project,Values=pywrkr` |

## 12. Cost Considerations

| Component | NAT/EIP Mode (3 regions, 3 workers each) | Public-IP Mode |
|-----------|------------------------------------------|----------------|
| NAT gateways | 9 × $0.045/hr = ~$0.41/hr | $0 |
| NAT data processing | $0.045/GB | $0 |
| Elastic IPs (public IPv4) | 9 × $0.005/hr = ~$0.045/hr (since Feb 2024, all public IPs cost $0.005/hr) | N/A |
| Fargate tasks | 12 tasks × ~$0.04/hr = ~$0.48/hr | Same |
| CloudWatch Logs | ~$0.50/GB ingested | Same |
| ECR storage | ~$0.10/GB/month | Same |
| **Hourly total (compute+NAT+EIP)** | **~$0.94/hr** | **~$0.48/hr** |

Recommendation: Use NAT/EIP mode for controlled testing where source IPs must be allowlisted. Use public-IP mode for cheaper exploratory tests.

## 13. Risk Register

| Risk | Mitigation |
|------|------------|
| Accidental load against production | `confirm_production` gate, max duration cap |
| NAT gateway cost growth | Default cleanup after run, cost tags for monitoring |
| Region quota limits (EIPs, NATs, Fargate tasks) | Document required quotas, fail fast on quota errors |
| Log retention cost | Default 7-day retention, configurable |
| Target allowlist coordination | Output all EIPs prominently, document allowlist process |
| Fargate ENI scaling | Document 250 ENI soft limit per region, request increase for large tests |
| Partial destroy failure | Tag-based orphan detection, document manual cleanup |

## 14. Rejected Alternatives

### Global Master with Cross-Region Workers
A single master in one region with workers in other regions would require cross-region TCP on port 9220, adding latency, complexity, and cross-region data transfer costs. Regional independence is simpler and more reliable.

### EKS/Kubernetes
EKS adds cluster management overhead, longer provisioning time (~15 min), and higher base cost. Fargate is pay-per-task with no idle compute. For ephemeral load test workloads, Fargate is the better fit.

### Step Functions Orchestration
Would replace Jenkins for AWS-native orchestration but adds complexity and requires the team to learn a new tool. Jenkins is already in use.

## 15. Future Improvements

- **EKS/Karpenter:** At very large scale (100+ workers), EKS with Karpenter spot nodes would be more cost-effective
- **Step Functions:** Replace Jenkins with AWS-native orchestration for teams that prefer it
- **Centralized observability:** Grafana Cloud or AWS Managed Grafana for cross-region dashboards
- **Spot-based optimization:** Fargate Spot for worker tasks (up to 70% cost reduction, with interruption risk)
- **Result storage:** S3 bucket for long-term result archival and trend analysis
- **Slack/Teams notifications:** Post test summaries to chat on completion
