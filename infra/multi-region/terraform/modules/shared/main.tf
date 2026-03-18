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
