# A master bound to 0.0.0.0 refuses to start without a secret (#303), so one is
# always set: the caller's, or a random one shared by this deployment's nodes.
resource "random_password" "worker_secret" {
  count   = var.worker_secret == null ? 1 : 0
  length  = 48
  special = false
}

locals {
  worker_secret = var.worker_secret != null ? var.worker_secret : random_password.worker_secret[0].result
}

# Delivered through the containers' `secrets`, not `environment`: a sensitive
# value inside jsonencode() would mark every container definition sensitive and
# hide image/command/target changes from `terraform plan`, and an environment
# entry would put the plaintext secret in DescribeTaskDefinition output.
resource "aws_ssm_parameter" "worker_secret" {
  name        = "/${var.name_prefix}/pywrkr/worker-secret"
  description = "pywrkr master/worker HMAC secret (PYWRKR_WORKER_SECRET)"
  type        = "SecureString"
  value       = local.worker_secret
  tags        = var.tags
}

# The execution role fetches `secrets` at task start. The parameter uses the
# AWS-managed aws/ssm key, which needs no separate kms:Decrypt grant.
resource "aws_iam_role_policy" "read_worker_secret" {
  name = "${var.name_prefix}-read-worker-secret"
  role = element(split("/", module.iam.execution_role_arn), length(split("/", module.iam.execution_role_arn)) - 1)
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameters"]
      Resource = aws_ssm_parameter.worker_secret.arn
    }]
  })
}

# IAM is eventually consistent: a task started right after the policy is
# written can still be denied ssm:GetParameters and stop before it runs.
resource "time_sleep" "worker_secret_policy_propagation" {
  create_duration = "15s"
  triggers = {
    policy = aws_iam_role_policy.read_worker_secret.policy
  }
}

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
  # The execution role must be able to read the secret before a task starts.
  depends_on = [time_sleep.worker_secret_policy_propagation]

  source = "../pywrkr-master"

  name_prefix           = var.name_prefix
  cluster_id            = module.ecs_cluster.cluster_id
  namespace_id          = module.ecs_cluster.namespace_id
  image                 = var.image
  cpu                   = var.master_cpu
  memory                = var.master_memory
  execution_role_arn    = module.iam.execution_role_arn
  task_role_arn         = module.iam.task_role_arn
  subnet_id             = var.egress_mode == "nat_eip" ? module.network.master_subnet_id : module.network.public_subnet_ids[0]
  security_group_id     = module.network.master_sg_id
  assign_public_ip      = var.egress_mode == "public_ip"
  log_group_name        = module.logging.master_log_group_name
  aws_region            = var.region_name
  cloudmap_namespace    = var.cloudmap_namespace
  worker_count          = var.worker_count
  target_url            = var.target_url
  test_duration         = var.test_duration
  users                 = var.users
  connections           = var.connections
  rate                  = var.rate
  thresholds            = var.thresholds
  scenario_file         = var.scenario_file
  pywrkr_tags           = var.pywrkr_tags
  otel_endpoint         = var.otel_endpoint
  prom_remote_write     = var.prom_remote_write
  worker_secret_arn     = aws_ssm_parameter.worker_secret.arn
  worker_secret_version = aws_ssm_parameter.worker_secret.version
  tags                  = var.tags
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
  worker_subnet_ids = (
    var.egress_mode == "nat_eip"
    ? module.network.worker_subnet_ids
    : [for i in range(var.worker_count) : module.network.public_subnet_ids[i % length(module.network.public_subnet_ids)]]
  )
  security_group_id = module.network.worker_sg_id
  assign_public_ip  = var.egress_mode == "public_ip"
  log_group_name    = module.logging.worker_log_group_name
  aws_region        = var.region_name
  master_dns        = module.master.dns_name
  tags              = var.tags

  depends_on            = [module.master]
  worker_secret_arn     = aws_ssm_parameter.worker_secret.arn
  worker_secret_version = aws_ssm_parameter.worker_secret.version
}
