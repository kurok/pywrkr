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
