terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Remote, locked state.
  #
  # State used to live in the Jenkins workspace. Any workspace wipe or agent
  # change orphaned a VPC, NAT gateway, ECS services and a Cloud Map namespace
  # that kept billing and that the next run could not destroy, because the run
  # no longer had the state describing them.
  #
  # Deliberately empty: the bucket, table and region are supplied at init with
  # -backend-config, so this file carries no account-specific names and a
  # missing configuration fails the init rather than silently writing state to
  # disk. See infra/jenkins/Jenkinsfile.
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "pywrkr"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
