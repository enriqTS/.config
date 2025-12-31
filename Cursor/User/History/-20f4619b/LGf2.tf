output "input_bucket_name" {
  description = "Name of the input S3 bucket"
  value       = module.storage.input_bucket_id
}

output "results_bucket_name" {
  description = "Name of the results S3 bucket"
  value       = module.storage.results_bucket_id
}

output "analisa_docs_function_name" {
  description = "Name of the analisa-docs Lambda function"
  value       = module.lambda.analisa_docs_function_name
}

output "analisa_docs_function_arn" {
  description = "ARN of the analisa-docs Lambda function"
  value       = module.lambda.analisa_docs_function_arn
}

output "api_gateway_endpoint" {
  description = "API Gateway endpoint URL"
  value       = module.api_gateway.api_endpoint
}

output "api_gateway_analyze_endpoint" {
  description = "Full endpoint URL for document analysis"
  value       = "${module.api_gateway.api_endpoint}/analisar"
}
