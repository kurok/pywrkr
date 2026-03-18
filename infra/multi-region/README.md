# Multi-Region pywrkr Infrastructure

Deploy distributed pywrkr load tests across multiple AWS regions simultaneously using ECS Fargate. Each region runs an independent master/worker cell with controlled source-IP diversity via per-subnet NAT gateway/EIP routing, turnkey Jenkins orchestration, and automatic cleanup. The infrastructure is ephemeral: deploy per test, destroy after.

---

## Table of Contents

- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Quick Start — Single Region](#quick-start--single-region)
- [Multi-Region Deployment](#multi-region-deployment)
- [Jenkins Pipeline](#jenkins-pipeline)
- [Egress Modes](#egress-modes)
- [Source IP Control](#source-ip-control)
- [Scenario Modes](#scenario-modes)
- [Observability](#observability)
- [Safety Controls](#safety-controls)
- [Cost Estimates](#cost-estimates)
- [Adding New Regions](#adding-new-regions)
- [Cleanup](#cleanup)
- [Troubleshooting](#troubleshooting)
- [Why ECS/Fargate](#why-ecsfargate)
- [Future Improvements](#future-improvements)

---

## Architecture

```
Jenkins Controller
│
├── 1. Build image ──▶ ECR (home region: us-east-1)
│                         │
│                         ├── replicates to ──▶ ECR eu-west-1
│                         └── replicates to ──▶ ECR ap-southeast-1
│
├── 2. Terraform apply (single root module)
│       │
│       ├── module "region_us_east_1"        ┐
│       ├── module "region_eu_west_1"        ├── count-gated by regions[].enabled
│       └── module "region_ap_southeast_1"   ┘
│
└── 3. Per-region test cell:
        ┌─────────────────────────────────────────────────────┐
        │  VPC (e.g., 10.1.0.0/16)                           │
        │                                                     │
        │  ┌──────────┐  Cloud Map: pywrkr.local              │
        │  │  Master   │◀── pywrkr-master.pywrkr.local:9220   │
        │  │ (Fargate) │                                       │
        │  └────┬──────┘                                       │
        │       │ TCP 9220                                     │
        │  ┌────┼────────┬────────────┐                        │
        │  │ Worker A    │ Worker B   │ Worker C    │          │
        │  │ subnet-a    │ subnet-b   │ subnet-c    │          │
        │  │   ▼         │   ▼        │   ▼         │          │
        │  │ nat-gw-a    │ nat-gw-b   │ nat-gw-c    │          │
        │  │ eip-a       │ eip-b      │ eip-c       │          │
        │  │ 54.x.x.1   │ 54.x.x.2  │ 54.x.x.3   │          │
        │  └─────────────┴────────────┴─────────────┘          │
        │       │              │             │                  │
        │       ▼              ▼             ▼                  │
        │     Target URL (HTTPS)                               │
        └─────────────────────────────────────────────────────┘
```

Each region is fully independent — no cross-region master/worker communication. Regions run their tests in parallel.

---

## Prerequisites

| Requirement | Minimum Version | Notes |
|-------------|----------------|-------|
| AWS CLI | v2 | Configured with credentials for all target regions |
| Terraform | >= 1.6 | |
| Docker | Recent stable | For building the pywrkr image |
| Jenkins | 2.x with Pipeline plugin | For automated orchestration (optional for manual runs) |

### IAM Permissions

The executing IAM principal needs permissions for:

- **ECR:** `ecr:CreateRepository`, `ecr:PutImage`, `ecr:PutReplicationConfiguration`, `ecr:DescribeImages`
- **ECS:** `ecs:CreateCluster`, `ecs:CreateService`, `ecs:RegisterTaskDefinition`, `ecs:DescribeServices`, `ecs:DeleteService`, `ecs:DeleteCluster`
- **EC2/VPC:** `ec2:CreateVpc`, `ec2:CreateSubnet`, `ec2:CreateNatGateway`, `ec2:AllocateAddress`, `ec2:ReleaseAddress`, `ec2:CreateSecurityGroup`, `ec2:*RouteTable*`, `ec2:*InternetGateway*`
- **Cloud Map:** `servicediscovery:CreatePrivateDnsNamespace`, `servicediscovery:CreateService`, `servicediscovery:DeleteNamespace`
- **CloudWatch Logs:** `logs:CreateLogGroup`, `logs:PutRetentionPolicy`, `logs:GetLogEvents`, `logs:DeleteLogGroup`
- **IAM:** `iam:CreateRole`, `iam:AttachRolePolicy`, `iam:PassRole`, `iam:DeleteRole`

### AWS Service Quotas

Check these quotas in **each** target region before deploying:

| Resource | Default Quota | Required (3 workers/region) |
|----------|--------------|----------------------------|
| Elastic IPs per region | 5 | 3 (NAT/EIP mode) |
| NAT gateways per AZ | 5 | Up to 3 |
| Fargate On-Demand tasks | 500 | 4 (1 master + 3 workers) |
| VPCs per region | 5 | 1 |
| ENIs per region | 250 (soft) | 4+ |

Request quota increases before running large-scale tests.

---

## Quick Start — Single Region

Deploy a minimal single-region test to verify the setup:

```bash
cd infra/multi-region/terraform

# 1. Copy and edit the example config
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` — enable only one region:

```hcl
regions = {
  "us-east-1" = {
    enabled       = true
    vpc_cidr      = "10.1.0.0/16"
    az_count      = 2
    worker_count  = 1
  }
  "eu-west-1" = {
    enabled = false
    vpc_cidr = "10.2.0.0/16"
  }
  "ap-southeast-1" = {
    enabled = false
    vpc_cidr = "10.3.0.0/16"
  }
}

target_url    = "https://example.com"
test_duration = 30
image_tag     = "latest"
```

```bash
# 2. Build and push the Docker image to ECR
#    (Or use the Jenkins pipeline which handles this automatically)
docker build -t pywrkr:latest -f ../Dockerfile ../../..
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com
docker tag pywrkr:latest <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/pywrkr:latest
docker push <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/pywrkr:latest

# 3. Deploy
terraform init
terraform plan
terraform apply

# 4. Check deployed EIPs (NAT/EIP mode)
terraform output nat_eips

# 5. Destroy when done
terraform destroy
```

---

## Multi-Region Deployment

Step-by-step for a full 3-region deployment:

**1. Configure all regions in `terraform.tfvars`:**

```hcl
home_region = "us-east-1"

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
  }
  "ap-southeast-1" = {
    enabled       = true
    vpc_cidr      = "10.3.0.0/16"
    az_count      = 2
    worker_count  = 3
  }
}

egress_mode   = "nat_eip"
target_url    = "https://api.example.com"
test_duration = 60
users         = 10
connections   = 10
```

VPC CIDRs must be unique across enabled regions (validated by Terraform).

**2. Build and push the image to the home region ECR:**

```bash
docker build -t pywrkr:v1.0 -f infra/multi-region/Dockerfile .
# Push to home region ECR — ECR replication handles the rest
```

**3. Wait for ECR replication** (usually under 60 seconds):

```bash
# Verify image is available in each target region
aws ecr describe-images --repository-name pywrkr --region eu-west-1 --image-ids imageTag=v1.0
aws ecr describe-images --repository-name pywrkr --region ap-southeast-1 --image-ids imageTag=v1.0
```

**4. Deploy:**

```bash
cd infra/multi-region/terraform
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

Terraform creates all three regional test cells in a single apply. Each region gets its own VPC, ECS cluster, Cloud Map namespace, master service, and worker services.

**5. Retrieve source IPs for allowlisting:**

```bash
terraform output nat_eips
# Returns: { "us-east-1" = ["54.x.x.1", "54.x.x.2", "54.x.x.3"], ... }
```

**6. Destroy after the test:**

```bash
terraform destroy
```

---

## Jenkins Pipeline

The Jenkinsfile at `jenkins/Jenkinsfile` provides full automation: build, deploy, run, collect results, and clean up.

### Configuration

Create a Jenkins Pipeline job pointing to `infra/multi-region/jenkins/Jenkinsfile`.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `AWS_REGIONS` | string | `us-east-1` | Comma-separated list of regions |
| `ENVIRONMENT` | string | `loadtest` | Environment name |
| `IMAGE_TAG` | string | `latest` | Docker image tag |
| `TARGET_URL` | string | (required) | URL to benchmark |
| `TEST_DURATION` | string | `60s` | Test duration |
| `USERS` | string | `10` | Virtual users per worker |
| `CONNECTIONS` | string | `10` | Concurrent connections per worker |
| `RATE` | string | `0` | Requests/sec per worker (0 = unlimited) |
| `WORKER_COUNT_PER_REGION` | string | `3` | Workers per region |
| `THRESHOLDS` | string | `p95 < 500ms,error_rate < 5%` | Comma-separated threshold expressions |
| `SCENARIO_FILE` | string | (empty) | Path to scenario JSON inside container |
| `EGRESS_MODE` | choice | `nat_eip` | `nat_eip` or `public_ip` |
| `CLEANUP_AFTER_RUN` | boolean | `true` | Destroy infrastructure after test |
| `CONFIRM_PRODUCTION_TARGET` | string | `no` | Must be `yes` for non-internal URLs |
| `OTEL_ENDPOINT` | string | (empty) | OTel collector URL |
| `PROM_REMOTE_WRITE` | string | (empty) | Prometheus pushgateway URL |
| `MASTER_CPU` | string | `1024` | Master task CPU units |
| `MASTER_MEMORY` | string | `2048` | Master task memory (MB) |
| `WORKER_CPU` | string | `1024` | Worker task CPU units |
| `WORKER_MEMORY` | string | `2048` | Worker task memory (MB) |

### Pipeline Stages

```
1. Checkout
2. Validate Parameters — safety checks (confirm_production, max duration, max workers)
3. Build Docker Image
4. Push to ECR (home region)
5. Wait for ECR Replication — polls aws ecr describe-images in each target region (60s timeout)
6. Terraform Init + Plan + Apply
7. Per-region (parallel):
   a. Wait for master service stability
   b. Wait for worker service stability
   c. Wait for test duration + buffer
   d. Fetch CloudWatch logs
   e. Extract results from master logs
8. Aggregate Results — combine per-region results into summary
9. Evaluate Thresholds — fail build if any region breached
10. Archive Artifacts — logs, results JSON, summary
11. Cleanup (if enabled) — terraform destroy
```

### Running

Trigger the job from the Jenkins UI or via CLI:

```bash
# Example: 3-region test against staging
jenkins-cli build pywrkr-multiregion \
  -p AWS_REGIONS=us-east-1,eu-west-1,ap-southeast-1 \
  -p TARGET_URL=https://staging.example.com \
  -p TEST_DURATION=120s \
  -p WORKER_COUNT_PER_REGION=3 \
  -p CONFIRM_PRODUCTION_TARGET=yes
```

Results are archived as `results/<region>/output.json` and `results/summary.json`.

---

## Egress Modes

The `egress_mode` variable controls how workers reach the internet.

### NAT/EIP Mode (`nat_eip`) — Default

Each worker service is pinned to a dedicated private subnet with its own NAT gateway and Elastic IP. Source IPs are stable, predictable, and can be allowlisted.

### Public-IP Mode (`public_ip`)

Workers are placed in public subnets with `assign_public_ip = true`. Each Fargate task gets an ephemeral public IP from the AWS pool. IPs are not stable and cannot be allowlisted.

### Cost Comparison (3 regions, 3 workers each)

| Component | NAT/EIP Mode | Public-IP Mode |
|-----------|-------------|----------------|
| NAT gateways | 9 x $0.045/hr = ~$0.41/hr | $0 |
| NAT data processing | $0.045/GB | $0 |
| Elastic IPs (public IPv4) | 9 x $0.005/hr = ~$0.045/hr | N/A |
| Fargate tasks | 12 tasks x ~$0.04/hr = ~$0.48/hr | ~$0.48/hr |
| CloudWatch Logs | ~$0.50/GB ingested | ~$0.50/GB ingested |
| ECR storage | ~$0.10/GB/month | ~$0.10/GB/month |
| **Hourly total (compute + network)** | **~$0.94/hr** | **~$0.48/hr** |

Use NAT/EIP mode when source IPs must be allowlisted. Use public-IP mode for cheaper exploratory tests where source IP stability does not matter.

---

## Source IP Control

In `nat_eip` mode, each worker gets a deterministic outbound IP:

```
worker-a service → private-subnet-a → route-table-a → nat-gw-a → eip-a (54.x.x.1)
worker-b service → private-subnet-b → route-table-b → nat-gw-b → eip-b (54.x.x.2)
worker-c service → private-subnet-c → route-table-c → nat-gw-c → eip-c (54.x.x.3)
```

Each worker ECS service is pinned to exactly one private subnet via `network_configuration.subnets`. That subnet's route table sends `0.0.0.0/0` to a dedicated NAT gateway with its own EIP.

### Retrieving EIPs for Allowlisting

```bash
# From Terraform outputs
terraform output nat_eips
# Returns map: region → list of EIP addresses

# Or query AWS directly using resource tags
aws ec2 describe-addresses \
  --filters Name=tag:project,Values=pywrkr \
  --query 'Addresses[].PublicIp' \
  --region us-east-1
```

Share the EIP list with the target system's operations team before running tests against firewalled endpoints.

---

## Scenario Modes

pywrkr supports three run modes, all configurable through `terraform.tfvars` or Jenkins parameters.

### Simple URL

Benchmark a single endpoint:

```hcl
target_url    = "https://api.example.com/health"
test_duration = 60
users         = 10
connections   = 10
scenario_file = ""
```

### Scenario File

Multi-step API flows using a scenario JSON file baked into the Docker image (placed in `/scenarios/`):

```hcl
scenario_file = "/scenarios/api-flow.json"
```

Scenario files are copied into the image from `infra/multi-region/scenarios/`. Add new scenarios to that directory and rebuild.

### HAR Import

Convert a HAR file to a scenario JSON, then use it as a scenario:

```bash
# Convert HAR to scenario JSON (run locally)
pywrkr har-import recording.har -o scenarios/har-example.json
```

Place the output JSON in `infra/multi-region/scenarios/`, rebuild the image, and set `scenario_file` accordingly.

---

## Observability

### OpenTelemetry

Set `otel_endpoint` to export traces and metrics to an OTel collector:

```hcl
otel_endpoint = "https://otel-collector.example.com:4318"
```

The Docker image includes the `[otel]` extra, so OTel dependencies are pre-installed.

### Prometheus

Set `prom_remote_write` to push metrics to a Prometheus Pushgateway:

```hcl
prom_remote_write = "https://pushgateway.example.com"
```

### CloudWatch Logs

All master and worker containers log to CloudWatch via the `awslogs` log driver. Log groups are created per service with configurable retention (`log_retention_days`, default 7).

Retrieve log group names:

```bash
terraform output log_group_names
```

### VPC Flow Logs

Enable network-level visibility with `enable_flow_logs = true`. Flow logs are sent to CloudWatch Logs with retention controlled by `flow_log_retention_days` (default 7).

---

## Safety Controls

| Control | How It Works |
|---------|-------------|
| Production target confirmation | `confirm_production` must be `"yes"` to target non-RFC1918/localhost URLs. Default: `"no"`. |
| Duration cap | `max_duration_seconds` (default 300) prevents runaway tests. Validated by both Jenkins and Terraform. |
| Worker cap | `max_workers_per_region` (default 10) limits blast radius. Terraform validates `worker_count` against this. |
| Cleanup by default | `CLEANUP_AFTER_RUN=true` in Jenkins — infrastructure is torn down after every test. |
| EIP release | EIPs have no `lifecycle.prevent_destroy` — `terraform destroy` releases them immediately. |
| Resource tagging | All resources tagged `project=pywrkr`, `environment=<env>`, `managed-by=terraform` for cost tracking and orphan detection. |

---

## Cost Estimates

Estimated costs for a 3-region deployment (us-east-1, eu-west-1, ap-southeast-1) with 3 workers per region in NAT/EIP mode:

| Component | Cost |
|-----------|------|
| NAT gateways (9) | ~$0.41/hr |
| NAT data processing | $0.045/GB |
| Elastic IPs (9) | ~$0.045/hr |
| Fargate tasks (12) | ~$0.48/hr |
| CloudWatch Logs | ~$0.50/GB ingested |
| ECR storage | ~$0.10/GB/month |
| **Total (compute + network)** | **~$0.94/hr** |

A typical 5-minute test costs under $0.10. Since infrastructure is destroyed after each run, there are no idle costs.

---

## Adding New Regions

To support a new region (e.g., `us-west-2`):

**1. Add a provider alias in `terraform/providers.tf`:**

```hcl
provider "aws" {
  alias  = "us_west_2"
  region = "us-west-2"
}
```

**2. Add a module block in `terraform/main.tf`:**

```hcl
module "region_us_west_2" {
  count     = try(var.regions["us-west-2"].enabled, false) ? 1 : 0
  source    = "./modules/regional"
  providers = { aws = aws.us_west_2 }
  # ... pass region config from var.regions["us-west-2"]
}
```

**3. Add the region to `terraform.tfvars`:**

```hcl
regions = {
  # ... existing regions ...
  "us-west-2" = {
    enabled      = true
    vpc_cidr     = "10.4.0.0/16"
    az_count     = 2
    worker_count = 3
  }
}
```

This is intentionally verbose because Terraform requires static provider alias references — `for_each` cannot be used with dynamic provider blocks.

---

## Cleanup

### Standard Cleanup

```bash
cd infra/multi-region/terraform
terraform destroy
```

This releases all resources: EIPs, NAT gateways, VPCs, ECS clusters, Cloud Map namespaces, IAM roles, and log groups. All associated costs stop immediately.

### Jenkins Cleanup

When `CLEANUP_AFTER_RUN=true` (the default), the Jenkins pipeline runs `terraform destroy` automatically after collecting results.

### Orphan Detection

If a destroy fails partway, find orphaned resources by tag:

```bash
# Find orphaned EIPs
aws ec2 describe-addresses \
  --filters Name=tag:project,Values=pywrkr Name=tag:environment,Values=loadtest \
  --region us-east-1

# Find orphaned NAT gateways
aws ec2 describe-nat-gateways \
  --filter Name=tag:project,Values=pywrkr \
  --region us-east-1

# Find orphaned VPCs
aws ec2 describe-vpcs \
  --filters Name=tag:project,Values=pywrkr \
  --region us-east-1
```

Repeat for each region. Manually delete any orphaned resources, starting with ECS services, then NAT gateways, then EIPs, then subnets, then VPCs.

---

## Troubleshooting

### ECR Replication Timeout

**Symptom:** Jenkins fails at "Wait for ECR Replication" stage.

**Cause:** ECR cross-region replication can take longer than 60 seconds for large images.

**Fix:** Verify replication rules exist (`aws ecr describe-registry`). Check that the image was pushed successfully to the home region. Re-run the pipeline — replication is eventually consistent.

### ECS Service Instability

**Symptom:** Services never reach steady state; tasks keep restarting.

**Cause:** Usually a health check failure on the master task, or workers unable to resolve `pywrkr-master.pywrkr.local`.

**Fix:**
1. Check master task logs: `aws ecs describe-tasks` and CloudWatch log group.
2. Verify Cloud Map namespace was created: `aws servicediscovery list-namespaces`.
3. Ensure security groups allow TCP 9220 between worker and master SGs.
4. Check that the image tag exists in the region's ECR.

### Log Collection Failures

**Symptom:** Jenkins cannot find results in CloudWatch logs.

**Cause:** The master container may not have finished writing results, or the log group name does not match expectations.

**Fix:** Check `terraform output log_group_names` for the correct log group. Verify the master task ran to completion. Look for `---PYWRKR_JSON_START---` / `---PYWRKR_JSON_END---` markers in the master log stream.

### DNS Resolution Failures

**Symptom:** Workers fail to connect to master with DNS errors.

**Cause:** Cloud Map namespace or service registration not yet propagated.

**Fix:** Cloud Map DNS propagation within a VPC is usually fast but can take up to 60 seconds. The worker task retries connections. If persistent, verify the master service registered with Cloud Map (`aws servicediscovery list-instances`). Ensure worker security group allows outbound DNS (UDP/TCP 53) to the VPC CIDR.

### Quota Exceeded Errors

**Symptom:** Terraform fails with `AddressLimitExceeded` or similar.

**Cause:** AWS service quota reached for EIPs, NAT gateways, or Fargate tasks in the target region.

**Fix:** Request a quota increase via the AWS Service Quotas console for the affected region and resource type.

---

## Why ECS/Fargate

ECS Fargate is pay-per-task with no cluster management overhead and provisions in seconds. EKS requires a control plane (~15 minutes to provision, ~$0.10/hr base cost) and adds Kubernetes operational complexity that is unnecessary for ephemeral load test workloads. Fargate tasks map naturally to pywrkr's master/worker model: one task per service, one service per worker subnet. For this use case, Fargate is simpler, faster to provision, and cheaper at moderate scale.

---

## Future Improvements

- **EKS/Karpenter:** At very large scale (100+ workers), EKS with Karpenter spot nodes would be more cost-effective than Fargate.
- **Step Functions:** Replace Jenkins with AWS-native orchestration for teams that prefer it.
- **Centralized observability:** Grafana Cloud or AWS Managed Grafana for cross-region dashboards.
- **Spot-based optimization:** Fargate Spot for worker tasks (up to 70% cost reduction, with interruption risk).
- **Result storage:** S3 bucket for long-term result archival and trend analysis.
- **Slack/Teams notifications:** Post test summaries to chat on completion.
