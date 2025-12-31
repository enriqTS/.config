# API Gateway REST API
resource "aws_api_gateway_rest_api" "docly_api" {
  name        = var.api_name
  description = "API Gateway for Docly Document Analysis"

  body = templatefile("${path.root}/../api-definition/openapi.json", {
    aws_region          = var.aws_region
    lambda_function_arn = var.analisa_docs_function_arn
  })

  tags = {
    Name        = "Docly API Gateway"
    Environment = var.environment
    Project     = "POC-Validacao-Documentos"
  }
}

# API Gateway Deployment
resource "aws_api_gateway_deployment" "api_deployment" {
  rest_api_id = aws_api_gateway_rest_api.docly_api.id

  triggers = {
    redeployment = sha1(jsonencode(aws_api_gateway_rest_api.docly_api.body))
  }

  lifecycle {
    create_before_destroy = true
  }
}

# API Gateway Stage
resource "aws_api_gateway_stage" "api_stage" {
  deployment_id = aws_api_gateway_deployment.api_deployment.id
  rest_api_id   = aws_api_gateway_rest_api.docly_api.id
  stage_name    = var.api_stage_name

  tags = {
    Name        = "Docly API Stage"
    Environment = var.environment
    Project     = "POC-Validacao-Documentos"
  }
}
