variable "aws_region" {
  description = "AWS region to deploy resources"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name (e.g., dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "input_bucket_name" {
  description = "Name of the S3 bucket for input documents"
  type        = string
  default     = "docly-documentos"
}

variable "results_bucket_name" {
  description = "Name of the S3 bucket for analysis results"
  type        = string
  default     = "docly-resultados-analise"
}

variable "analisa_docs_function_name" {
  description = "Name of the analisa-docs Lambda function"
  type        = string
  default     = "docly-analisa-docs"
}

variable "api_gateway_name" {
  description = "Name of the API Gateway"
  type        = string
  default     = "docly-api"
}

variable "api_stage_name" {
  description = "Name of the API Gateway stage"
  type        = string
  default     = "dev"
}
