# Terraform Import Configuration
# This file defines imports for existing AWS resources
# Run: terraform plan to see what will be imported
# Then: terraform apply to complete the import

# Lambda Functions - Only importing the ones with active (uncommented) resource definitions

# responder-mensagem (ACTIVE)
# envia-template (ACTIVE)

# Instructions for adding more imports:
# 1. Uncomment the resource definition in the Lambda module's lambda.tf file
# 2. Ensure the resource name matches the function name (e.g., aws_lambda_function.envia-template)
# 3. Add an import block here following the pattern above
# 4. Run terraform plan to verify
# 5. Run terraform apply to import

# Template for adding new Lambda imports:
# import {
#   to = module.compute.module.lambda.module.<MODULE_NAME>.aws_lambda_function.<RESOURCE_NAME>
#   id = "<AWS_FUNCTION_NAME>"
# }

# Example for when you define busca-conversas:
# import {
#   to = module.compute.module.lambda.module.busca-conversas.aws_lambda_function.busca-conversas
#   id = "busca-conversas"
# }

# ===================================================================
# IAM Roles and Policies
# ===================================================================
# ===================================================================
# DynamoDB Tables
# ===================================================================

# kpis table
import {
  to = module.databases.aws_dynamodb_table.kpis
  id = "kpis"
}

# embarques table
import {
  to = module.databases.aws_dynamodb_table.embarques
  id = "embarques"
}

# motoristas_sessoes table
import {
  to = module.databases.aws_dynamodb_table.motoristas_sessoes
  id = "motoristas_sessoes"
}

# equipamentos table (with GSIs)
import {
  to = module.databases.aws_dynamodb_table.equipamentos
  id = "equipamentos"
}

# negociacao table (with GSIs)
import {
  to = module.databases.aws_dynamodb_table.negociacao
  id = "negociacao"
}

# veiculos table (with GSI)
import {
  to = module.databases.aws_dynamodb_table.veiculos
  id = "veiculos"
}

# motoristas table (with GSI)
import {
  to = module.databases.aws_dynamodb_table.motoristas
  id = "motoristas"
}

# ofertas table
import {
  to = module.databases.aws_dynamodb_table.ofertas
  id = "ofertas"
}

# api_errors table
import {
  to = module.databases.aws_dynamodb_table.api_errors
  id = "api_errors"
}
