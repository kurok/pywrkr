# -----------------------------------------------------------------------------
# Command construction — mirrors existing infra pattern
# Wraps pywrkr in sh -c to capture JSON results with markers for log parsing.
# -----------------------------------------------------------------------------

locals {
  base_cmd        = ["--master", "--expect-workers", tostring(var.worker_count), "--bind", "0.0.0.0", "--port", tostring(var.coordination_port)]
  duration_cmd    = ["-d", tostring(var.test_duration)]
  connections_cmd = var.users == 0 ? ["-c", tostring(var.connections)] : []
  users_cmd       = var.users > 0 ? ["-u", tostring(var.users)] : []
  rate_cmd        = var.rate > 0 ? ["--rate", tostring(var.rate)] : []
  threshold_cmd   = flatten([for th in var.thresholds : ["--threshold", th]])
  tag_cmd         = flatten([for k, v in var.pywrkr_tags : ["--tag", "${k}=${v}"]])
  otel_cmd        = var.otel_endpoint != "" ? ["--otel-endpoint", var.otel_endpoint] : []
  prom_cmd        = var.prom_remote_write != "" ? ["--prom-remote-write", var.prom_remote_write] : []
  scenario_cmd    = var.scenario_file != "" ? ["--scenario", var.scenario_file] : []
  url_cmd         = var.scenario_file == "" ? [var.target_url] : []

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
