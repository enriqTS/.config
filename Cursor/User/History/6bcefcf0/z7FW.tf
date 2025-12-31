# IAM Role for Lambda
resource "aws_iam_role" "lambda_role" {
  name               = "${var.function_name}-role"
  assume_role_policy = file("${path.root}/../iam-policies/lambda-trust-policy.json")

  tags = {
    Name        = "Docly Lambda Execution Role"
    Environment = var.environment
    Project     = "POC-Validacao-Documentos"
  }
}

# IAM Policy for Lambda
resource "aws_iam_role_policy" "lambda_policy" {
  name = "${var.function_name}-policy"
  role = aws_iam_role.lambda_role.id

  policy = templatefile("${path.root}/../iam-policies/lambda-execution-policy.json", {
    input_bucket_name   = var.input_bucket_name
    results_bucket_name = var.results_bucket_name
  })
}
