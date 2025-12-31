# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Location

**Working Directory:**
- Windows path: `C:\upd8\Rodosafra IA`
- Git Bash path: `/c/upd8/Rodosafra%20IA`

All file paths in this document are relative to this root directory unless otherwise specified.

## Overview

Rodosafra is an automated WhatsApp chatbot system for managing cargo offers and shipments for truck drivers. The system integrates with WhatsApp via chat API, uses AWS Bedrock agents for natural language processing, and manages driver/vehicle registrations and cargo offers through a REST API.

### Company Contact Information

**Address:**
Avenida Belo Horizonte, 1120 - Campo Belo/MG

**CEO:**
Daniel Henrique Pinheiro

**Contact:**
- Email: daniel@rodosafra.com
- Website: https://rodosafratransportes.com.br/

The chatbot can provide this information to drivers when they request direct contact with the company.

### Common Abbreviations Understanding

The chatbot is trained to understand common Brazilian Portuguese abbreviations used by truck drivers in WhatsApp messages. This includes informal language patterns such as:
- Affirmative/Negative: s (sim), n (não), blz (beleza), ok, ss (sim sim)
- Pronouns/Verbs: vc (você), to (estou), ta (está), eh (é)
- Questions: oq (o que), pq (porque), qdo (quando), qto (quanto)
- Common words: tb/tbm (também), mt/mto (muito), msm (mesmo), td (tudo), hj (hoje), agr (agora), amnh (amanhã), pra (para), vlw (valeu), flw (falou)
- Transport/Vehicles: km, kg, ton, carga, frete, placa, doc, cpf, cnh, rntrc

**Important**: The chatbot never corrects drivers or asks them to write in full - it simply interprets abbreviations correctly and responds professionally.

### Vehicle Type Questions

When asking drivers about their vehicle type during **registration/cadastro**:
- **DO NOT** present a list of vehicle options
- **DO NOT** use the term "conjunto" - ask about individual vehicles
- **DO** ask in two steps:
  1. First: "É cavalo ou caminhão?" (Is it a tractor or truck?)
  2. If cavalo: "Que tipo de cavalo?" (What type of tractor?)
  3. If caminhão: No need to ask subtype - proceed to next step
- Let the driver respond freely - the system will map their response to the correct vehicle IDs automatically
- Keep questions simple and conversational

**When talking about EXISTING registered vehicles (verification/confirmation):**
- You CAN use the term "conjunto" to refer to the driver's registered set of vehicles
- Example: "Encontrei aqui seu conjunto" or "Esse é o conjunto completo que você usa?"

**For offer searches:**
- The vehicle type ID passed is the conjunto ID
- If driver mentions conjunto + equipment → use both
- If driver mentions only conjunto AND it can have equipment (trucks, trailers) → ask "Qual tipo de equipamento?"
- If driver mentions only conjunto AND it's a tractor without equipment (Cavalo Simples, Cavalo Trucado, etc.) → don't ask about equipment

## System Architecture

### Multi-Tier Architecture
The system operates across several AWS services:
- **Lambda Functions**: Orchestration, chatbot processing, request handlers, validation
- **DynamoDB**: Session management, driver data, vehicle data, negotiation state
- **AWS Bedrock**: AI agent for natural language conversation
- **ECS Tasks**: WebSocket server for real-time WhatsApp messaging
- **Parameter Store**: Centralized authentication token management

### Message Flow

**Active Flow** (System initiates):
1. System sends template message via WhatsApp to drivers
   - **Urgent offers** (`carga_urgente=true`) override active negotiation check
   - Normal offers skip drivers in active negotiation (< 1 hour)
2. WebSocket connection established (ECS)
3. Conversation assumed by the chatbot
4. Messages processed through chatbot Lambda
5. Responses sent back through WebSocket

**Passive Flow** (Driver initiates):
1. Lambda polls for new messages in logistics sector every 10 seconds
2. Unassigned conversations are automatically assumed
3. WebSocket connection established
4. Message history retrieved and stored
5. Messages processed through chatbot orchestration

### Authentication Flow
- API authentication uses username/password → returns cookie token
- Token stored in Parameter Store as SecureString (`/rodosafra/auth/token`)
- Token metadata stored separately (`/rodosafra/auth/token-metadata`)
- Token valid for 24 hours
- `atualiza-auth-token.py` Lambda refreshes token and notifies WebSocket server
- All other services read token from Parameter Store (no local caching beyond 5 minutes)

### Session Management

**Design Patterns (CRITICAL - Must be followed in all code):**
1. **SessionId in SessionAttributes**: The `session_id` MUST ALWAYS be included in the `sessionAttributes` passed to Bedrock agent
2. **ISO 8601 Timestamp Format**: ALL timestamps MUST use ISO 8601 format (`YYYY-MM-DDTHH:MM:SSZ` or `YYYY-MM-DDTHH:MM:SS.ffffffZ`)
   - Session timestamps: `YYYY-MM-DDTHH:MM:SSZ` (hour precision for sort key)
   - Memory timestamps: `YYYY-MM-DDTHH:MM:SS.ffffffZ` (full precision)
   - Precise timestamps: `YYYY-MM-DDTHH:MM:SS.ffffffZ` (microsecond precision for matching)

**Implementation Details:**
- Session ID format: `{telefone}_{tempo_sessao}` where `tempo_sessao` is ISO 8601 format
- Session timestamp stored in `negociacao` DynamoDB table
- Sessions valid for 1 hour
- Expired sessions trigger new session creation
- Session attributes passed to Bedrock agent include: session_id, id_motorista, nome, telefone, cadastrado_telefone, cadastrado_cpf, data_atual, memory_id
- **IMPORTANT - PHONE NUMBER STANDARDIZATION**: All phone numbers are now stored **WITH** country code "55" prefix (e.g., `5532984214353` - 13 digits). This applies to ALL tables including `negociacao`, `motoristas`, etc. Phone numbers are automatically normalized by `normalizar_telefone()` function in handlers.

### Agent Architecture (Multi-Agent Design)
The system has:
- **Supervisor Agent**: Routes messages, handles transfers (transbordo), determines audio vs text mode
- **Agent 1**: Driver and vehicle registration
- **Agent 2**: Checking new cargo offers
- **Agent 3**: Creating shipments (embarques)
Further information is in the instructions files for the agents.

## Key APIs

### Rodosafra Public API (`rodosafra-api.json`)
Base URL (staging): `https://api-staging.rodosafra.net/api`

**Authentication**:
```
POST /publico/login
Body: { "usuario": "...", "senha": "..." }
Returns: { "token": "...", "nomeCookie": "..." }
Use: Cookie: {nomeCookie}={token}
```

**Core Endpoints**:
- `POST /publico/carga/ofertas` - Query available cargo offers
- `POST /publico/embarque` - Create shipment
- `GET/POST /publico/motorista/v1/cadastro` - Driver registration/verification (step 1)
- `POST /publico/motorista/v1/cadastro-automatico` - Driver automatic complete registration (step 2, background)
- `GET/POST /publico/veiculo/v1/cadastro` - Vehicle registration/verification (step 1)
- `POST /publico/veiculo/v1/cadastro-automatico` - Vehicle automatic complete registration (step 2, background)
- `GET /publico/veiculo/v1/lista/tipoVeiculo` - List vehicle types
- `GET /publico/veiculo/v1/lista/tipoEquipamento` - List equipment types

**Registration Flow (Two-Step Process)**:
Both driver and vehicle registrations now follow a two-step process:
1. **Step 1** (`/cadastro`): Initial registration - Returns ID on success (201)
2. **Step 2** (`/cadastro-automatico`): Complete automatic registration - Background process
   - Called automatically after step 1 succeeds
   - Result is ignored - runs in background
   - Only 500 errors are logged (not 400 errors)
   - Main flow continues regardless of step 2 result

### Chat Service API (`rodosafra-chat-api.json`)
Base URL: `http://api-gateway.rodosafra.net:8952`

**WebSocket Management**:
- `GET /api/chat/notificacao/conversas/registrar` - SSE endpoint for real-time updates
- `PUT /api/chat/conversa/usuario` - Assign/unassign conversation to user
- `PUT /api/chat/conversa/setor` - Change conversation sector
- `PUT /api/chat/conversa/visualizar-mensagens` - Mark messages as read
- `POST /api/chat/conversa/mensagem/template` - Send WhatsApp template message

**Conversation Management**:
- `GET /api/chat/conversa/lista/setores` - List conversations by sector
- `GET /api/chat/mensagens` - List paginated messages for conversation

## Development Commands

### Testing API Endpoints
The repository includes Postman collection (`Rodosafra.postman_collection.json`) for manual testing.

### Running Python Scripts
Each script is a standalone Lambda function with specific environment variables:

## Important Implementation Notes

### Driver Registration Scenarios (V3.0)
The system handles four distinct scenarios based on dual flags:
1. **CENARIO 1**: `cadastrado_telefone=true` AND `cadastrado_cpf=true` → Fully registered driver
2. **CENARIO 2**: `cadastrado_telefone=false` AND `cadastrado_cpf=true` → **Registration Issue** - CPF exists but different phone - Transfer to registration team
3. **CENARIO 3**: `cadastrado_telefone=true` AND `cadastrado_cpf=false` → **Registration Issue** - Phone exists but different CPF - Transfer to registration team
4. **CENARIO 4**: Both false → New registration required

**IMPORTANT**: When CENARIO 2 or 3 is detected, DO NOT mention "security", "fraud", or ANY problems to the driver. The driver does not know about the issue - it was discovered internally. Simply say naturally "Só um minuto que já te retorno" (Just a moment, I'll get back to you) and transfer immediately to registration team via transbordo.

**FRAUD DETECTION IMPLEMENTATION**: The `verificar-motorista-action-group.py` now automatically detects CENARIO 2 during CPF verification:
- When API returns a registered driver (status 200), it compares the session phone number with the registered phone number
- If phones differ, returns `status: "divergencia_telefone"` with instructions for immediate transbordo
- The chatbot receives clear instructions to say "Só um minuto que já te retorno" and call `fazer_transbordo` with `motivo='divergencia_cadastro_cpf'`
- All phone numbers are normalized (with/without "55" prefix) before comparison to ensure accuracy

### Webhook and Validation Pattern
Each request type has two components:
1. **Validation** (`requests/validation/*-validate.py`) - Validates incoming webhook data
2. **Handler** (`requests/handler/*-handler.py`) - Processes validated data

Event types:
- `embarque-aceito` - Shipment accepted
- `embarque-cancelado` - Shipment cancelled
- `oferta-indisponivel` - Offer no longer available
- `recebe-info-oferta` - Receive offer information
- `ofertas-disponiveis` - Available offers

## File Organization

**Note**: The project is organized with each Lambda function in its own directory for Terraform deployment.

```
/api-specs              - API specifications and schemas
  - rodosafra-api.json          - Public API OpenAPI spec
  - rodosafra-chat-api.json     - Chat service API spec
  - Rodosafra.postman_collection.json - Postman collection for testing

/arquitetura            - Architecture diagrams and design docs
  - rodosafra.drawio            - System architecture diagram (editable)
  - rodosafra.drawio.png        - Architecture diagram (PNG export)

/auxiliares             - Shared utility modules
  - api_error_logger.py         - API error logging to DynamoDB
  - api_retry_util.py           - API retry logic with exponential backoff
  - ecs_ip_resolver.py          - ECS task IP resolution for WebSocket
  - kpi_tracker.py              - KPI tracking and metrics logging
  - lambda_retry_util.py        - Lambda invocation retry wrapper
  - message_history.py          - Message history management
  - transbordo_caller.py        - Transfer to human team helper

/cadastros              - Driver/vehicle verification Lambda functions
  /verifica-cadastro-motorista  - Driver verification Lambda
    - lambda_function.py        - Verify driver registration
  /verifica-cadastro-veiculo    - Vehicle verification Lambda
    - lambda_function.py        - Verify vehicle registration

/chat                   - WebSocket chat functionality
  /server               - WebSocket server (ECS task)
    - server.js         - Node.js WebSocket server implementation
    - package.json      - Node.js dependencies
    - Dockerfile        - Container definition for ECS
  /websocket            - WebSocket Lambda functions
    /busca-conversas    - Passive flow Lambda (driver initiates)
      - busca-conversas.py      - Poll and assume new conversations
    /envia-template     - Active flow Lambda (system initiates)
      - envia-template.py       - Send WhatsApp template messages
      - [local copies of utilities]
    /responder-mensagem - Main message responder Lambda
      - lambda_function.py      - Process incoming messages
      - [local copies of utilities]
    - poll-eventbridge-rule.json - EventBridge rule for polling

/chatbot                - Main chatbot and action groups
  /chatbot              - Main chatbot orchestration Lambda
    - lambda_function.py        - Main chatbot orchestration
  /action-groups        - Bedrock agent action group Lambdas
    /verificar-motorista-action-group  - CPF verification with fraud detection
      - lambda_function.py
    /verificar-veiculo-action-group    - Plate verification
      - lambda_function.py
    /cadastra-motorista-action-group   - Driver registration
      - lambda_function.py
    /cadastra-veiculo-action-group     - Vehicle registration
      - lambda_function.py
    /consultar-ofertas-action-group    - Query cargo offers
      - lambda_function.py
    /criar-embarque-action-group       - Create shipment
      - lambda_function.py
    /transbordo-action-group           - Transfer to human team
      - lambda_function.py
    /flag-audio-action-group           - Audio mode flag handler
      - lambda_function.py
  /instrucoes           - Agent instruction files
    /multi-agente       - Multi-agent system instructions
      - supervisor.md   - Supervisor agent instructions (routing)
      - supervisor-agent.md - Supervisor agent schema
      - cadastro.md     - Registration agent instructions
      - cadastro-agent.md - Registration agent schema
      - ofertas.md      - Offers agent instructions
      - ofertas-agent.md - Offers agent schema
      - embarque.md     - Shipment agent instructions
      - embarque-agent.md - Shipment agent schema

/claude-changes-docs    - Documentation of Claude Code changes
  - VERIFICACAO-IMEDIATA-CHANGES.md     - Immediate verification flow implementation
  - ACTION-GROUPS-DATA-VERIFICATION.md  - Data verification analysis
  - ENVIA-TEMPLATE-BUGFIX.md            - Template send bugfix documentation
  - FRAUD-DETECTION-VERIFICATION.md     - Fraud detection in verification action group
  - DATA-INHERITANCE-FIX.md             - Vehicle data inheritance fix
  - ISO-TIMESTAMP-FIX.md                - ISO 8601 timestamp standardization
  - EMBARQUE-PAYLOAD-FIX.md             - Embarque API response payload return fix

/docs                   - General project documentation
  - Especificação Cadastros.pdf         - Registration specifications
  - Especificação e Glossário.pdf       - System glossary
  - Especificação Rodosafrav2.pdf       - Main system specification
  - Tipos de veículo.pdf                - Vehicle types documentation
  - Websocket - Serviço Chat.pdf        - WebSocket service documentation
  - Documentação SSE do serviço de chat.pdf - SSE implementation docs

/exemplos-json          - JSON examples and reference data
  - veiculos_id.json                    - Vehicle type IDs
  - equipamentos_id.json                - Equipment type IDs
  - exemplo-cadastro.json               - Registration example

/iam-policies           - IAM policy documents for Lambda functions
  - responder-msgs-policy.json          - Policy for responder-mensagem Lambda
  - envia-template-policy.json          - Policy for envia-template Lambda
  - [additional policies as created]

/orquestracao           - Orchestration layer Lambda functions
  /atualiza-auth-token  - Token refresh Lambda
    - lambda_function.py        - Token refresh automation

/requests               - Webhook handler and validator Lambda functions
  /handler              - Process validated webhook events
    /ofertas-disponiveis-handler - Available offers handler Lambda
      - lambda_function.py
    /embarque-aceito-handler     - Shipment accepted handler Lambda
      - lambda_function.py
    /embarque-cancelado-handler  - Shipment cancelled handler Lambda
      - lambda_function.py
    /oferta-indisponivel-handler - Offer unavailable handler Lambda
      - lambda_function.py
    /recebe-info-oferta-handler  - Receive offer info handler Lambda
      - lambda_function.py
  /validation           - Validate incoming webhook data
    /ofertas-disponiveis-validate - Validate available offers Lambda
      - lambda_function.py
    /embarque-aceito-validate     - Validate shipment accepted Lambda
      - lambda_function.py
    /embarque-cancelado-validate  - Validate shipment cancelled Lambda
      - lambda_function.py
    /oferta-indisponivel-validate - Validate offer unavailable Lambda
      - lambda_function.py
    /recebe-info-oferta-validate  - Validate receive offer info Lambda
      - lambda_function.py
  /examples             - Webhook payload examples

/requests-layer         - Lambda layer for shared dependencies
  /python               - Python packages for Lambda layer

/terraform              - Infrastructure as Code
  /environment          - Environment-specific configurations
  /modules              - Reusable Terraform modules

Root files:
  - CLAUDE.md           - Project instructions for Claude Code
  - README.md           - Project overview and setup instructions (Portuguese)
  - anotacoes.md        - Project notes and observations
```

## Data Flow Examples

### Querying Cargo Offers
1. Driver: "Quero ver ofertas de carga para amanhã"
2. Chatbot extracts intent → calls `consultar-ofertas-action-group.py`
3. Action group calls `POST /publico/carga/ofertas` with filters
4. API returns list of `CargaOfertaResposta` objects
5. Chatbot formats and presents offers to driver

### Creating Shipment
1. Driver accepts an offer
2. Chatbot verifies driver registration (`verificar-cadastro-motorista.py`)
3. Chatbot verifies vehicle registration (`verificar-cadastro-veiculo.py`)
4. If incomplete → triggers registration flow
5. If complete → calls `criar-embarque-action-group.py`
6. Action group calls `POST /publico/embarque` with all required IDs
7. Returns embarque ID to driver

### Transfer to Human (Transbordo)
Triggers include:
- Driver insists on price changes repeatedly
- Driver appears to be giving up on offer
- Too many API errors occur
- CPF mismatch scenario (registration issue - CENARIO 2 and 3)
- After 3 failed CPF verification attempts
- **Phone number issues** (CRITICAL - AI can NEVER update phone numbers):
  - Driver requests to change phone number
  - System detects different phone numbers (current contact vs registered)
  - Any phone number discrepancy must trigger immediate transfer

When triggered, `call-transbordo.py` transfers conversation to registration team sector.

**Message to driver**:
- For all transfer cases (fraud detection, registration issues, phone changes): "Só um minuto que já te retorno" (natural response)
- **CRITICAL**: NEVER mention problems, issues, differences, or verification to the driver
- The driver is unaware of internal fraud/mismatch detection - keep conversation natural

**Phone Number Policy**:
- **AI CANNOT update/change phone numbers under ANY circumstances**
- If phone numbers differ or driver requests change → immediate transbordo
- Never attempt to correct or update phone - always transfer to human team

## System Scale
Per `anotacoes.txt`:
- ~400 offers distributed to 5-10 drivers each
- ~4000 daily messages
- **Urgent offers** (`carga_urgente=true`):
  - Sent immediately regardless of driver status
  - **Override active negotiation check** - sent even if driver is already in conversation
  - Non-urgent offers skip drivers in active negotiation (< 1 hour session)

## WebSocket Connection Management
- WebSocket stays connected during active conversation
- After 1 hour of conversation: disconnect from WebSocket, listen via SSE
- On new SSE event: reconnect to WebSocket
- Always mark messages as read when assuming conversation

## Common Pitfalls

1. **Token Management**: Always read token from Parameter Store. Never cache for more than 5 minutes. Token expires after 24 hours.

2. **Session ID Format**: Must always be `{telefone}_{tempo_sessao}`. Breaking this format breaks session continuity.

3. **Phone Number Standardization**: All phone numbers MUST be normalized to 13-digit format with "55" country code prefix (e.g., `5532984214353`) across ALL tables and operations. Phone normalization is automatically applied at Lambda entry points via `normalizar_telefone()` function. This standardization applies to:
   - `negociacao` table
   - `motoristas` table
   - All WebSocket communications
   - All API calls
   - All DynamoDB queries

4. **Vehicle Types**: API expects `idTipoVeiculo` and `idTipoEquipamento` (integers), not names. Use list endpoints to get valid IDs.

5. **Embarque Creation**: Requires `motoristaId`, `veiculoCavaloOuCaminhaoId`, `cargaId`, `pesoEstimadoEmbarque`, `previsaoEmbarque` (datetime in ISO format).

6. **WebSocket vs REST**: Real-time messages use WebSocket (ECS), but conversation metadata updates use REST API.