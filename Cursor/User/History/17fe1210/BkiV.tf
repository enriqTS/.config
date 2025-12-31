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

  backend "s3" {
    bucket = "value"
    key = "value"
    region = "us-east-1"
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "POC-Validacao-Documentos"
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}

module "storage" {
  source = "./modules/storage"

  input_bucket_name   = var.input_bucket_name
  results_bucket_name = var.results_bucket_name
  environment         = var.environment
}

module "lambda" {
  source = "./modules/lambda"

  analisa_docs_function_name = var.analisa_docs_function_name
  aws_region                 = var.aws_region
  input_bucket_name          = var.input_bucket_name
  results_bucket_name        = var.results_bucket_name
  api_gateway_execution_arn  = module.api_gateway.api_execution_arn
  environment                = var.environment

  depends_on = [module.storage]
}

module "api_gateway" {
  source = "./modules/api-gateway"

  api_name                   = var.api_gateway_name
  api_stage_name             = var.api_stage_name
  analisa_docs_function_arn  = module.lambda.analisa_docs_function_arn
  resultado_function_arn     = module.lambda.resultado_function_arn
  aws_region                 = var.aws_region
  environment                = var.environment
}
