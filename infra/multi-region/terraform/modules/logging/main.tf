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
