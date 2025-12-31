# Lambda Function: Popula Tabelas de Referência

Esta Lambda function popula as tabelas DynamoDB de equipamentos e veículos com dados de referência.

## Estrutura

- `lambda_function.py` - Função principal do Lambda
- `example-input.json` - Exemplo de formato de entrada

## Uso

### Input do Evento

A função espera um evento JSON com a seguinte estrutura:

```json
{
  "equipamentos": [
    {
      "id": 1,
      "nomeTipoEquipamento": "Basculante"
    }
  ],
  "veiculos": [
    {
      "id": 1,
      "nomeTipoVeiculo": "VUC",
      "cavaloOuCaminhao": true,
      "podePossuirEquipamento": true
    }
  ]
}
```

Ambas as listas (`equipamentos` e `veiculos`) são opcionais. Você pode fornecer apenas uma delas se necessário.

### Variáveis de Ambiente

- `EQUIPAMENTOS_TABLE` - Nome da tabela DynamoDB para equipamentos (padrão: `equipamentos`)
- `VEICULOS_TABLE` - Nome da tabela DynamoDB para veículos (padrão: `veiculos`)

### Output

A função retorna um JSON com o seguinte formato:

```json
{
  "statusCode": 200,
  "body": {
    "success": true,
    "message": "Operação concluída",
    "results": {
      "equipamentos": {
        "success": true,
        "count": 20,
        "errors": []
      },
      "veiculos": {
        "success": true,
        "count": 19,
        "errors": []
      },
      "overall_success": true
    }
  }
}
```

## Como usar com os arquivos JSON existentes

Para usar os arquivos `equipamentos_id.json` e `veiculos_id.json` da pasta `exemplos-json`:

1. Combine os dois arquivos JSON em um único evento:

```bash
# Exemplo usando jq (se disponível)
jq -s '{equipamentos: .[0], veiculos: .[1]}' \
  ../exemplos-json/equipamentos_id.json \
  ../exemplos-json/veiculos_id.json > input.json
```

2. Ou crie manualmente um arquivo JSON combinando os dois:

```json
{
  "equipamentos": [/* conteúdo de equipamentos_id.json */],
  "veiculos": [/* conteúdo de veiculos_id.json */]
}
```

3. Invoke a Lambda function com esse JSON:

```bash
aws lambda invoke \
  --function-name popula-tabelas-referencia \
  --payload file://input.json \
  response.json
```

## Estrutura das Tabelas DynamoDB

### Tabela de Equipamentos

- **Partition Key**: `id` (Number)
- **Atributos**:
  - `id` (Number) - ID do equipamento
  - `nomeTipoEquipamento` (String) - Nome do tipo de equipamento
  - `created_at` (String) - Timestamp de criação (ISO format)
  - `updated_at` (String) - Timestamp de atualização (ISO format)

### Tabela de Veículos

- **Partition Key**: `id` (Number)
- **Atributos**:
  - `id` (Number) - ID do veículo
  - `nomeTipoVeiculo` (String) - Nome do tipo de veículo
  - `cavaloOuCaminhao` (Boolean) - Indica se é cavalo ou caminhão
  - `podePossuirEquipamento` (Boolean) - Indica se pode possuir equipamento
  - `created_at` (String) - Timestamp de criação (ISO format)
  - `updated_at` (String) - Timestamp de atualização (ISO format)

## Permissões IAM Necessárias

A Lambda function precisa das seguintes permissões:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:BatchWriteItem",
        "dynamodb:PutItem"
      ],
      "Resource": [
        "arn:aws:dynamodb:*:*:table/equipamentos",
        "arn:aws:dynamodb:*:*:table/veiculos"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:*"
    }
  ]
}
```

## Tratamento de Erros

- A função usa `batch_writer` para inserções eficientes
- Erros individuais são capturados e reportados sem interromper o processamento
- Se algum item falhar, ele será reportado no array `errors` do resultado
- A função continua processando os demais itens mesmo se alguns falharem

