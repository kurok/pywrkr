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
# All worker services share one task definition and one log group.
# Individual streams are differentiated by awslogs-stream-prefix + service name.
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
