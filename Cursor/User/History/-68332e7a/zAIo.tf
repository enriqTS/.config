# __generated__ by Terraform
# Please review these resources and move them into your main configuration files.

data "archive_file" "envia_template_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../../../../../chat/websocket/envia-template"
  output_path = "${path.module}/../../../../build/lambda/envia-template.zip"
}

# __generated__ by Terraform
resource "aws_lambda_function" "envia-template" {
  architectures                      = ["x86_64"]
  filename                           = data.archive_file.envia_template_zip.output_path
  function_name                      = "envia-template"
  handler                            = "lambda_function.lambda_handler"
  layers                             = [var.layer_arn]
  memory_size                        = 128
  package_type                       = "Zip"
  region                             = "us-east-1"
  reserved_concurrent_executions     = -1
  role                               = "arn:aws:iam::700308877414:role/service-role/envia-template-role-1uec1cvy"
  runtime                            = "python3.11"
  skip_destroy                       = false
  source_code_hash                   = data.archive_file.envia_template_zip.output_base64sha256
  timeout                            = 630
  environment {
    variables = {
      API_BASE_URL        = "https://api-gateway.rodosafra.net:8760"
      CHATBOT_LAMBDA_NAME = "chatbot"
      ECS_WEBSOCKET_URL   = "http://100.26.57.244:3000"
      TEMPLATE_NOME       = "enviar_oferta_carga_motorista"
      USUARIO_RESPONSAVEL = "assitente.virtual"
    }
  }
  ephemeral_storage {
    size = 512
  }
  logging_config {
    application_log_level = null
    log_format            = "Text"
    log_group             = "/aws/lambda/envia-template"
    system_log_level      = null
  }
  tracing_config {
    mode = "PassThrough"
  }
}