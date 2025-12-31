provider "aws" {
    region = "us-east-1"
}

terraform {
    required_providers {
        aws = {
            source = "hashicorp/aws"
            version = "~> 6.0"
        }
    }
    backend "s3" {
        bucket = "rodosafra-terraform-state"
        key = "dev/terraform.tfstate"
        region = "us-east-1"
    }
}

module "compute" {
  source = "../../modules/compute"
}

module "databases" {
  source = "../../modules/databases"
}

module "ai" {
  source = "../../modules/ai"
}