data "archive_file" "responder_mensagem_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../../../../../chat/websocket/responder-mensagem"
  output_path = "${path.module}/../../../../build/lambda/responder-mensagem.zip"
}

resource "aws_lambda_function" "responder-mensagem" {
  architectures                      = ["x86_64"]
  filename                           = data.archive_file.responder_mensagem_zip.output_path
  function_name                      = "responder-mensagem"
  handler                            = "lambda_function.lambda_handler"
  layers                             = [var.layer_arn]
  memory_size                        = 256
  package_type                       = "Zip"
  region                             = "us-east-1"
  reserved_concurrent_executions     = -1
  role                               = aws_iam_role.responder-mensagem-role-lfmb0m6m.arn
  runtime                            = "python3.11"
  skip_destroy                       = false
  source_code_hash                   = data.archive_file.responder_mensagem_zip.output_base64sha256
  timeout                            = 450
  environment {
    variables = {
      MOTORISTAS_SESSOES_TABLE = "motoristas_sessoes"
      MOTORISTAS_TABLE         = "motoristas"
    }
  }
  ephemeral_storage {
    size = 512
  }
  logging_config {
    log_format            = "Text"
    log_group             = "/aws/lambda/responder-mensagem"
  }
  tracing_config {
    mode = "PassThrough"
  }
}