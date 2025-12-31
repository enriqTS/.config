# Lambda Function Archive
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.root}/../analisa-docs-function"
  output_path = "${path.root}/.terraform/lambda-function.zip"
}

# Lambda Function
resource "aws_lambda_function" "processing_function" {
  filename         = data.archive_file.lambda_zip.output_path
  function_name    = var.function_name
  role            = var.lambda_role_arn
  handler         = "lambda_function.lambda_handler"
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  runtime         = "python3.12"
  timeout         = 300
  memory_size     = 512

  environment {
    variables = {
      INPUT_BUCKET   = var.input_bucket_name
      RESULTS_BUCKET = var.results_bucket_name
    }
  }

  tags = {
    Name        = "Docly Document Analysis Function"
    Environment = var.environment
    Project     = "POC-Validacao-Documentos"
  }
}

# CloudWatch Log Group
resource "aws_cloudwatch_log_group" "lambda_log_group" {
  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = 7

  tags = {
    Name        = "Docly Lambda Logs"
    Environment = var.environment
    Project     = "POC-Validacao-Documentos"
  }
}

# Lambda Permission for API Gateway
resource "aws_lambda_permission" "api_gateway_permission" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.processing_function.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${var.api_gateway_execution_arn}/*/*"
}
