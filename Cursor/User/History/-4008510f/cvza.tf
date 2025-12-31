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
