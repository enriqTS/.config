terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "Docly"
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}

# Storage Module - S3 Buckets
module "storage" {
  source = "./modules/storage"

  input_bucket_name   = var.input_bucket_name
  results_bucket_name = var.results_bucket_name
  environment         = var.environment
}

# Lambda Module - Lambda Function and API Gateway
module "lambda" {
  source = "./modules/lambda"

  function_name       = var.lambda_function_name
  api_name            = var.api_gateway_name
  api_stage_name      = var.api_stage_name
  input_bucket_name   = var.input_bucket_name
  results_bucket_name = var.results_bucket_name
  aws_region          = var.aws_region
  environment         = var.environment

  depends_on = [module.storage]
}
