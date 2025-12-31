# ===================================================================
# Additional Lambda Function Imports
# Generated for bulk import with -generate-config-out
# ===================================================================

# Main Functions
import {
  to = module.compute.module.lambda.module.busca-conversas.aws_lambda_function.busca-conversas
  id = "buscar-conversas"  # Note: AWS name is "buscar-conversas", module is "busca-conversas"
}

import {
  to = module.compute.module.lambda.module.envia-template.aws_lambda_function.envia-template
  id = "envia-template"
}

# Request Handlers
import {
  to = module.compute.module.lambda.module.embarque-aceito-handler.aws_lambda_function.embarque-aceito-handler
  id = "embarque-aceito-handler"
}

import {
  to = module.compute.module.lambda.module.embarque-cancelado-handler.aws_lambda_function.embarque-cancelado-handler
  id = "embarque-cancelado-handler"
}

import {
  to = module.compute.module.lambda.module.oferta-indisponivel-handler.aws_lambda_function.oferta-indisponivel-handler
  id = "oferta-indisponivel-handler"
}

import {
  to = module.compute.module.lambda.module.ofertas-disponiveis-handler.aws_lambda_function.ofertas-disponiveis-handler
  id = "ofertas-disponiveis-handler"
}

import {
  to = module.compute.module.lambda.module.recebe-info-oferta-handler.aws_lambda_function.recebe-info-oferta-handler
  id = "recebe-info-oferta-handler"
}

# Request Validators
import {
  to = module.compute.module.lambda.module.embarque-aceito-validate.aws_lambda_function.embarque-aceito-validate
  id = "embarque-aceito-validate"
}

import {
  to = module.compute.module.lambda.module.embarque-cancelado-validate.aws_lambda_function.embarque-cancelado-validate
  id = "embarque-cancelado-validate"
}

import {
  to = module.compute.module.lambda.module.oferta-indisponivel-validate.aws_lambda_function.oferta-indisponivel-validate
  id = "oferta-indisponivel-validate"
}

import {
  to = module.compute.module.lambda.module.ofertas-disponiveis-validate.aws_lambda_function.ofertas-disponiveis-validate
  id = "ofertas-disponiveis-validate"
}

import {
  to = module.compute.module.lambda.module.recebe-info-oferta-validate.aws_lambda_function.recebe-info-oferta-validate
  id = "recebe-info-oferta-validate"
}

# Webhooks
import {
  to = module.compute.module.lambda.module.webhook-cadastro-motorista.aws_lambda_function.webhook-cadastro-motorista
  id = "cadastro-motorista-webhook"
}

import {
  to = module.compute.module.lambda.module.webhook-cadastro-veiculo.aws_lambda_function.webhook-cadastro-veiculo
  id = "cadastro-veiculo-webhook"
}

# Cadastros
import {
  to = module.compute.module.lambda.module.verifica-cadastro-motorista.aws_lambda_function.verifica-cadastro-motorista
  id = "verifica-cadastro-motorista"
}

import {
  to = module.compute.module.lambda.module.verifica-cadastro-veiculo.aws_lambda_function.verifica-cadastro-veiculo
  id = "verifica-cadastro-veiculo"
}

# Orquestração
import {
  to = module.compute.module.lambda.module.atualiza-auth-token.aws_lambda_function.atualiza-auth-token
  id = "atualiza-auth-token"
}

# Action Groups (Bedrock Agent Actions)
import {
  to = module.compute.module.lambda.module.cadastra-motorista-action-group.aws_lambda_function.cadastra-motorista-action-group
  id = "cadastro_motorista-sj96o"
}

import {
  to = module.compute.module.lambda.module.cadastra-veiculo-action-group.aws_lambda_function.cadastra-veiculo-action-group
  id = "cadastro_veiculo-w6ajm"
}

import {
  to = module.compute.module.lambda.module.consultar-ofertas-action-group.aws_lambda_function.consultar-ofertas-action-group
  id = "consulta_ofertas-pybro"
}

import {
  to = module.compute.module.lambda.module.criar-embarque-action-group.aws_lambda_function.criar-embarque-action-group
  id = "cria-embarque-fbcon"
}

import {
  to = module.compute.module.lambda.module.flag-audio-action-group.aws_lambda_function.flag-audio-action-group
  id = "flag_audio-gekjh"
}

import {
  to = module.compute.module.lambda.module.flag-duvida-embarque-action-group.aws_lambda_function.flag-duvida-embarque-action-group
  id = "Flag-Duvida-Embarque-hoqke"
}

import {
  to = module.compute.module.lambda.module.transbordo-action-group.aws_lambda_function.transbordo-action-group
  id = "transbordo-fqkhj"
}

import {
  to = module.compute.module.lambda.module.verificar-motorista-action-group.aws_lambda_function.verificar-motorista-action-group
  id = "verificar-motorista-29igj"
}

import {
  to = module.compute.module.lambda.module.verificar-veiculo-action-group.aws_lambda_function.verificar-veiculo-action-group
  id = "verificar-veiculo-c5yj9"
}

# Note: The following Lambda functions exist in AWS but don't have modules:
# - chatbot (main orchestrator)
# - popula-dynamo-tipos (utility)
# - reset_audio_lambda (utility)
# - teste-audio-trasncribe (utility)
# - verificar_tipo_veiculo_permitido-679fg (utility)
# Create modules for these if you want to manage them with Terraform

# ===================================================================
# IAM Roles for Lambda Functions
# ===================================================================

# Main Functions
import {
  to = module.compute.module.lambda.module.busca-conversas.aws_iam_role.role
  id = "buscar-conversas-role-r5h66uro"
}

# Request Handlers
import {
  to = module.compute.module.lambda.module.embarque-aceito-handler.aws_iam_role.role
  id = "embarque-aceito-handler-role-xae16ivj"
}

import {
  to = module.compute.module.lambda.module.embarque-cancelado-handler.aws_iam_role.role
  id = "embarque-cancelado-handler-role-uiv82gxy"
}

import {
  to = module.compute.module.lambda.module.oferta-indisponivel-handler.aws_iam_role.role
  id = "oferta-indisponivel-handler-role-m3byunn4"
}

import {
  to = module.compute.module.lambda.module.ofertas-disponiveis-handler.aws_iam_role.role
  id = "ofertas-disponiveis-handler-role-f03zr14v"
}

import {
  to = module.compute.module.lambda.module.recebe-info-oferta-handler.aws_iam_role.role
  id = "recebe-info-oferta-handler-role-7fg62u87"
}

# Request Validators
import {
  to = module.compute.module.lambda.module.embarque-aceito-validate.aws_iam_role.role
  id = "embarque-aceito-validate-role-on5cof8e"
}

import {
  to = module.compute.module.lambda.module.embarque-cancelado-validate.aws_iam_role.role
  id = "embarque-cancelado-validate-role-a3oh6xhj"
}

import {
  to = module.compute.module.lambda.module.oferta-indisponivel-validate.aws_iam_role.role
  id = "oferta-indisponivel-validate-role-i6srzhao"
}

import {
  to = module.compute.module.lambda.module.ofertas-disponiveis-validate.aws_iam_role.role
  id = "ofertas-disponiveis-validate-role-qrfn1b7a"
}

import {
  to = module.compute.module.lambda.module.recebe-info-oferta-validate.aws_iam_role.role
  id = "recebe-info-oferta-validate-role-q5lbcbkd"
}

# Webhooks
import {
  to = module.compute.module.lambda.module.webhook-cadastro-motorista.aws_iam_role.role
  id = "cadastro-motorista-webhook-role-q5rg0nxv"
}

import {
  to = module.compute.module.lambda.module.webhook-cadastro-veiculo.aws_iam_role.role
  id = "cadastro-veiculo-webhook-role-ybhajenf"
}

# Cadastros
import {
  to = module.compute.module.lambda.module.verifica-cadastro-motorista.aws_iam_role.role
  id = "verifica-cadastro-motorista-role-93sg0tly"
}

import {
  to = module.compute.module.lambda.module.verifica-cadastro-veiculo.aws_iam_role.role
  id = "verifica-cadastro-veiculo-role-5rahtskj"
}

# Orquestração
import {
  to = module.compute.module.lambda.module.atualiza-auth-token.aws_iam_role.role
  id = "atualiza-auth-token-role-ka3v3gyl"
}

# Action Groups
import {
  to = module.compute.module.lambda.module.cadastra-motorista-action-group.aws_iam_role.role
  id = "cadastro_motorista-sj96o-role-BNZV6TODAC9"
}

import {
  to = module.compute.module.lambda.module.cadastra-veiculo-action-group.aws_iam_role.role
  id = "cadastro_veiculo-w6ajm-role-3WOF897Q7X6"
}

import {
  to = module.compute.module.lambda.module.consultar-ofertas-action-group.aws_iam_role.role
  id = "consulta_ofertas-pybro-role-NT7GRAU495"
}

import {
  to = module.compute.module.lambda.module.criar-embarque-action-group.aws_iam_role.role
  id = "cria-embarque-fbcon-role-U034RWTAVGF"
}

import {
  to = module.compute.module.lambda.module.flag-audio-action-group.aws_iam_role.role
  id = "flag_audio-gekjh-role-1FRG0BBWJ2V"
}

import {
  to = module.compute.module.lambda.module.flag-duvida-embarque-action-group.aws_iam_role.role
  id = "Flag-Duvida-Embarque-hoqke-role-6MCE1QDSC0K"
}

import {
  to = module.compute.module.lambda.module.transbordo-action-group.aws_iam_role.role
  id = "transbordo-fqkhj-role-DVUNYOJMFWA"
}

import {
  to = module.compute.module.lambda.module.verificar-motorista-action-group.aws_iam_role.role
  id = "verificar-motorista-29igj-role-1I9H0JLVGFE"
}

import {
  to = module.compute.module.lambda.module.verificar-veiculo-action-group.aws_iam_role.role
  id = "verificar-veiculo-c5yj9-role-9NC6RNJDZA6"
}
