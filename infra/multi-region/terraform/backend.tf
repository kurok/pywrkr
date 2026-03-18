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
