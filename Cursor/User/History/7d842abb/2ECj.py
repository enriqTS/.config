"""
Action group para cadastro de veiculo na API Rodosafra
Registra novo veiculo apos verificacao de cadastro inexistente
"""
import json
import os
import logging
import boto3
import re
import requests
from typing import Dict, Any, List, Tuple, Optional
from botocore.exceptions import ClientError
from datetime import datetime
from decimal import Decimal
from api_error_logger import log_api_error
from api_retry_util import retry_on_timeout

logger = logging.getLogger()
logger.setLevel(logging.INFO)

API_BASE_URL = os.environ.get('RODOSAFRA_API_BASE_URL', 'https://api-staging.rodosafra.net/api')

dynamodb = boto3.resource('dynamodb')
TABELA_VEICULOS = os.environ.get('TABELA_VEICULOS', 'veiculos')
TABELA_EQUIPAMENTOS = os.environ.get('TABELA_EQUIPAMENTOS', 'equipamentos')

ssm_client = boto3.client('ssm')

PARAMETER_STORE_TOKEN_NAME = os.environ.get(
    'PARAMETER_STORE_TOKEN_NAME',
    '/rodosafra/auth/token'
)

auth_cookie = None

REQUEST_TIMEOUT = 15


def autenticar_api() -> Tuple[bool, Optional[str]]:
    """
    Obtem token de autenticacao do Parameter Store AWS

    Input: None
    Output: (tuple) - (sucesso: bool, cookie_ou_erro: str)
    """
    global auth_cookie

    if auth_cookie:
        logger.info("[AUTH] Token em cache reutilizado")
        return True, auth_cookie

    logger.info(f"[AUTH] Buscando token no Parameter Store: {PARAMETER_STORE_TOKEN_NAME}")

    try:
        response = ssm_client.get_parameter(
            Name=PARAMETER_STORE_TOKEN_NAME,
            WithDecryption=True
        )

        token = response['Parameter']['Value']

        if not token:
            logger.error("[AUTH] Token vazio no Parameter Store")
            return False, "Token vazio no Parameter Store"

        auth_cookie = token

        logger.info("[AUTH] Token obtido com sucesso")
        return True, auth_cookie

    except ClientError as e:
        error_code = e.response['Error']['Code']

        if error_code == 'ParameterNotFound':
            logger.error(f"[AUTH] Token nao encontrado: {PARAMETER_STORE_TOKEN_NAME}")
            return False, "Token nao encontrado no Parameter Store"

        elif error_code == 'AccessDeniedException':
            logger.error("[AUTH] Sem permissao para acessar Parameter Store")
            return False, "Sem permissao para acessar token"

        else:
            logger.error(f"[AUTH] Erro ao acessar Parameter Store: {error_code}")
            return False, f"Erro ao obter token: {error_code}"

    except Exception as e:
        logger.error(f"[AUTH] Erro inesperado: {str(e)}")
        return False, f"Erro inesperado: {str(e)}"


def _obter_valor_com_prioridade(params: Dict, session: Dict, possible_names: list) -> str:
    """
    Obtem valor priorizando parametros sobre session attributes

    Input: params (dict) - Parametros do action group
           session (dict) - Atributos da sessao
           possible_names (list) - Lista de nomes possiveis em ordem de prioridade
    Output: (str) - Valor encontrado ou string vazia
    """
    if isinstance(possible_names, str):
        possible_names = [possible_names]

    for name in possible_names:
        valor = params.get(name)
        if valor:
            logger.info(f"[VALIDACAO] Valor obtido dos parametros - key: {name}")
            return valor

    for name in possible_names:
        valor = session.get(name)
        if valor:
            logger.info(f"[VALIDACAO] Valor obtido da sessao - key: {name}")
            return valor

    logger.warning(f"[VALIDACAO] Valor nao encontrado - Nomes buscados: {possible_names}")
    return ''


def _limpar_placa(placa: str) -> str:
    """
    Remove espacos e caracteres especiais da placa

    Input: placa (str) - Placa com ou sem formatacao
    Output: (str) - Placa limpa em maiusculas
    """
    if not placa:
        return ""
    return re.sub(r'[^A-Z0-9]', '', placa.upper())


def _limpar_numeros(valor: str) -> str:
    """
    Remove caracteres nao numericos

    Input: valor (str) - Valor com ou sem numeros
    Output: (str) - Apenas digitos
    """
    return re.sub(r'\D', '', str(valor))


def salvar_veiculo_action_group_dynamodb(
    placa: str,
    codigo_veiculo: int,
    dados_cadastrados: Dict[str, Any],
    session_attributes: Dict[str, Any],
    params: Dict[str, Any]
) -> Tuple[bool, Optional[str]]:
    """
    Salva informacoes do veiculo no DynamoDB apos cadastro bem-sucedido

    Input: placa (str) - Placa do veiculo
           codigo_veiculo (int) - ID retornado pela API
           dados_cadastrados (dict) - Dados do cadastro
           session_attributes (dict) - Atributos da sessao
           params (dict) - Parametros do action group
    Output: (tuple) - (sucesso: bool, erro: str)
    """
    try:
        table_veiculos = dynamodb.Table(TABELA_VEICULOS)

        id_veiculo = str(codigo_veiculo)

        id_motorista = (
            session_attributes.get('id_motorista') or
            session_attributes.get('idMotorista') or
            session_attributes.get('motorista_id')
        )

        if not id_motorista:
            cpf = session_attributes.get('motorista_cpf') or session_attributes.get('cpf', '')
            cpf = cpf.replace('.', '').replace('-', '')
            if cpf:
                id_motorista = f"CPF_{cpf}"
            else:
                id_motorista = 'SEM_MOTORISTA'
            logger.warning(f"[DYNAMODB] ID do motorista nao encontrado, usando: {id_motorista}")

        id_motorista = str(id_motorista)

        logger.info(f"[DYNAMODB] Salvando veiculo {placa} - ID veiculo: {id_veiculo}, ID motorista: {id_motorista}")

        timestamp = datetime.utcnow().isoformat() + 'Z'

        def converter_para_decimal(obj):
            if isinstance(obj, dict):
                return {k: converter_para_decimal(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [converter_para_decimal(i) for i in obj]
            elif isinstance(obj, float):
                return Decimal(str(obj))
            else:
                return obj

        response = table_veiculos.get_item(
            Key={
                'id_veiculo': id_veiculo,
                'id_motorista': id_motorista
            }
        )
        item_existe = 'Item' in response

        nome_motorista = (
            session_attributes.get('motorista_nome') or
            session_attributes.get('nome_motorista') or
            session_attributes.get('nome') or
            'Motorista'
        )

        telefone_motorista = (
            session_attributes.get('motorista_telefone') or
            session_attributes.get('telefone') or
            ''
        )

        equipamento_ids = []
        total_equipamentos = 0

        try:
            equipamentos_table_name = os.environ.get('EQUIPAMENTOS_TABLE', 'equipamentos')
            equipamentos_table = dynamodb.Table(equipamentos_table_name)

            logger.info(f"[DYNAMODB] Buscando equipamentos associados ao veiculo_id: {id_veiculo}")

            from boto3.dynamodb.conditions import Key

            response_equipamentos = equipamentos_table.query(
                IndexName='id_veiculo',
                KeyConditionExpression=Key('id_veiculo').eq(str(id_veiculo))
            )

            if 'Items' in response_equipamentos and len(response_equipamentos['Items']) > 0:
                for equip_item in response_equipamentos['Items']:
                    equip_id = equip_item.get('id_equipamento')
                    if equip_id:
                        try:
                            equipamento_ids.append(Decimal(str(equip_id)))
                        except (ValueError, TypeError):
                            logger.warning(f"[DYNAMODB] ID de equipamento invalido: {equip_id}")

                total_equipamentos = len(equipamento_ids)
                logger.info(f"[DYNAMODB] Encontrados {total_equipamentos} equipamentos associados")
            else:
                logger.info("[DYNAMODB] Nenhum equipamento associado encontrado")

        except Exception as e:
            logger.warning(f"[DYNAMODB] Erro ao buscar equipamentos associados: {str(e)}")

        item = {
            'id_veiculo': id_veiculo,
            'id_motorista': id_motorista,
            'placa': placa,
            'rntrc': dados_cadastrados.get('rntrc'),
            'tipo_veiculo_id': dados_cadastrados.get('idTipoVeiculo'),
            'tipo_equipamento_id': dados_cadastrados.get('idTipoEquipamento'),
            'cpf_cnpj_proprietario': dados_cadastrados.get('cpfCnpjProprietario'),
            'equipamento_ids': equipamento_ids if equipamento_ids else None,
            'total_equipamentos': total_equipamentos,
            'motorista_nome': nome_motorista,
            'motorista_telefone': telefone_motorista,
            'dados_cadastrados': converter_para_decimal(dados_cadastrados),
            'updated_at': timestamp,
            'source': 'action_group_cadastro'
        }

        if not item_existe:
            item['created_at'] = timestamp
        else:
            item['created_at'] = response['Item'].get('created_at', timestamp)

        item = {k: v for k, v in item.items() if v is not None}

        table_veiculos.put_item(Item=item)

        logger.info(f"[DYNAMODB] Veiculo salvo com sucesso - Placa: {placa}, Tipo: {dados_cadastrados.get('idTipoVeiculo')}")

        return True, None

    except Exception as e:
        error_msg = f"Erro ao salvar veiculo no DynamoDB: {str(e)}"
        logger.error(f"[DYNAMODB] {error_msg}", exc_info=True)
        return False, error_msg


def cadastrar_veiculo(params: Dict, session: Dict) -> Dict[str, Any]:
    """
    Cadastra veiculo na API Rodosafra em dois passos

    Input: params (dict) - Parametros da funcao com dados do veiculo
           session (dict) - Atributos da sessao com dados adicionais
    Output: (dict) - Status do cadastro e codigo do veiculo
    """
    logger.info("[CADASTRO] Iniciando processo de cadastro de veiculo")

    placa_raw = _obter_valor_com_prioridade(
        params,
        session,
        ['placa', 'veiculo_placa', 'veiculo_principal_placa']
    )

    rntrc_raw = _obter_valor_com_prioridade(
        params,
        session,
        ['rntrc', 'veiculo_rntrc', 'veiculo_principal_renavam', 'renavam']
    )

    cpf_cnpj_proprietario_raw = _obter_valor_com_prioridade(
        params,
        session,
        ['cpf_cnpj_proprietario', 'cpf', 'motorista_cpf', 'cpf_proprietario', 'cpfCnpjProprietario']
    )

    tipo_veiculo_id = _obter_valor_com_prioridade(
        params,
        session,
        ['tipo_veiculo_id', 'id_tipo_veiculo', 'veiculo_principal_tipo_id', 'veiculo_tipo_id', 'idTipoVeiculo']
    )

    tipo_equipamento_id = _obter_valor_com_prioridade(
        params,
        session,
        ['tipo_equipamento_id', 'id_tipo_equipamento', 'equipamento_tipo_id', 'idTipoEquipamento']
    )

    placa_limpa = _limpar_placa(placa_raw)
    rntrc_limpo = _limpar_numeros(rntrc_raw)
    cpf_cnpj_proprietario_limpo = _limpar_numeros(cpf_cnpj_proprietario_raw)

    logger.info(f"[CADASTRO] Dados extraidos - Placa: {placa_limpa}, RNTRC: {len(rntrc_limpo)} digitos, CPF/CNPJ: {len(cpf_cnpj_proprietario_limpo)} digitos, Tipo: {tipo_veiculo_id}")

    erros = []

    if not placa_limpa or len(placa_limpa) != 7:
        erros.append(f"Placa invalida (deve ter 7 caracteres, recebido: {len(placa_limpa)})")

    if not rntrc_limpo or len(rntrc_limpo) < 8:
        erros.append(f"RNTRC invalido (minimo 8 digitos, recebido: {len(rntrc_limpo)})")

    if not cpf_cnpj_proprietario_limpo or len(cpf_cnpj_proprietario_limpo) not in [11, 14]:
        erros.append(f"CPF/CNPJ do proprietario invalido (deve ter 11 ou 14 digitos, recebido: {len(cpf_cnpj_proprietario_limpo)})")

    if not tipo_veiculo_id:
        erros.append("Tipo de veiculo nao informado")

    if erros:
        logger.error(f"[VALIDACAO] Erros encontrados: {erros}")
        return {
            "status": "erro",
            "mensagem": "Dados invalidos ou incompletos",
            "erros": erros
        }

    autenticado, auth_ou_erro = autenticar_api()
    if not autenticado:
        logger.error(f"[AUTH] Falha na autenticacao: {auth_ou_erro}")
        return {
            "status": "erro",
            "mensagem": f"Erro de autenticacao: {auth_ou_erro}"
        }

    payload = {
        "placa": placa_limpa,
        "rntrc": rntrc_limpo,
        "cpfCnpjProprietario": cpf_cnpj_proprietario_limpo,
        "idTipoVeiculo": int(tipo_veiculo_id)
    }

    if tipo_equipamento_id:
        payload["idTipoEquipamento"] = int(tipo_equipamento_id)

    logger.info(f"[CADASTRO] Payload preparado")

    telefone = session.get('telefone') or session.get('conversa_id')

    try:
        url = f"{API_BASE_URL}/publico/veiculo/v1/cadastro"

        logger.info(f"[API] Chamando endpoint de cadastro inicial - Step 1")
        logger.info(f"[API] Payload para {url}: {json.dumps(payload, ensure_ascii=False)}")

        response = retry_on_timeout(
            lambda: requests.post(
                url,
                json=payload,
                headers={
                    'Cookie': auth_cookie,
                    'Content-Type': 'application/json'
                },
                timeout=REQUEST_TIMEOUT
            ),
            max_retries=3,
            operation_name="Cadastrar veiculo (step 1)",
            telefone=telefone
        )

        logger.info(f"[API] Resposta recebida - Status: {response.status_code}")

        if response.status_code == 201:
            codigo_veiculo = response.json()

            logger.info(f"[CADASTRO] Veiculo cadastrado com sucesso - Codigo: {codigo_veiculo}")

            try:
                url_automatico = f"{API_BASE_URL}/publico/veiculo/v1/cadastro-automatico"

                logger.info(f"[API] Iniciando cadastro automatico completo - Step 2")
                logger.info(f"[API] Payload para {url_automatico}: {json.dumps(payload, ensure_ascii=False)}")

                response_automatico = requests.post(
                    url_automatico,
                    json=payload,
                    headers={
                        'Cookie': auth_cookie,
                        'Content-Type': 'application/json'
                    },
                    timeout=REQUEST_TIMEOUT
                )

                logger.info(f"[API] Cadastro automatico - Status: {response_automatico.status_code}")

                if response_automatico.status_code >= 500:
                    log_api_error(
                        api_route="/publico/veiculo/v1/cadastro-automatico",
                        error_code=response_automatico.status_code,
                        error_message=f"Erro no cadastro automatico completo (HTTP {response_automatico.status_code})",
                        payload=payload,
                        response_body=response_automatico.text
                    )
                    logger.warning(f"[API] Cadastro automatico falhou, mas step 1 teve sucesso")

            except Exception as e:
                logger.warning(f"[API] Erro no cadastro automatico (step 2), mas continuando: {str(e)}")

            sucesso_db, erro_db = salvar_veiculo_action_group_dynamodb(
                placa=placa_limpa,
                codigo_veiculo=codigo_veiculo,
                dados_cadastrados={
                    "placa": placa_limpa,
                    "rntrc": rntrc_limpo,
                    "cpfCnpjProprietario": cpf_cnpj_proprietario_limpo,
                    "idTipoVeiculo": tipo_veiculo_id,
                    "idTipoEquipamento": tipo_equipamento_id if tipo_equipamento_id else None
                },
                session_attributes=session,
                params=params
            )

            if not sucesso_db:
                logger.warning(f"[DYNAMODB] Falha ao salvar: {erro_db}")

            return {
                "status": "sucesso",
                "mensagem": f"Veiculo {placa_limpa} cadastrado com sucesso na Rodosafra",
                "codigo_veiculo": codigo_veiculo,
                "dados_cadastrados": {
                    "placa": placa_limpa,
                    "rntrc": rntrc_limpo,
                    "cpfCnpjProprietario": cpf_cnpj_proprietario_limpo,
                    "idTipoVeiculo": tipo_veiculo_id
                },
                "salvo_dynamodb": sucesso_db
            }

        elif response.status_code == 400:
            erro_api = response.json()
            mensagem_erro = erro_api.get('mensagem', 'Erro de validacao')

            logger.error(f"[API] Erro 400 - Validacao: {mensagem_erro}")

            return {
                "status": "erro",
                "mensagem": f"Dados invalidos: {mensagem_erro}",
                "detalhe_api": erro_api
            }

        elif response.status_code == 409:
            logger.warning(f"[API] Veiculo {placa_limpa} ja cadastrado")

            try:
                erro_api = response.json()
                mensagem_api = erro_api.get('mensagem', 'Placa ja cadastrada')
            except:
                mensagem_api = 'Placa ja cadastrada'

            return {
                "status": "ja_existe",
                "mensagem": f"Veiculo {placa_limpa} ja esta cadastrado",
                "sugestao": "Verifique se o cadastro ja foi feito anteriormente",
                "detalhes": mensagem_api
            }

        elif response.status_code == 500:
            logger.error("[API] Erro interno no servidor")

            log_api_error(
                api_route="/publico/veiculo/v1/cadastro",
                error_code=500,
                error_message="Erro interno no servidor ao cadastrar veiculo",
                payload=payload,
                response_body=response.text
            )

            return {
                "status": "erro",
                "mensagem": "Erro interno no servidor - tente novamente em alguns instantes"
            }

        else:
            logger.error(f"[API] Status inesperado: {response.status_code}")

            if response.status_code >= 500:
                log_api_error(
                    api_route="/publico/veiculo/v1/cadastro",
                    error_code=response.status_code,
                    error_message=f"Erro inesperado ao cadastrar veiculo (HTTP {response.status_code})",
                    payload=payload,
                    response_body=response.text
                )

            return {
                "status": "erro",
                "mensagem": f"Erro inesperado ao cadastrar (HTTP {response.status_code})",
                "detalhe": response.text[:200] if response.text else ""
            }

    except requests.exceptions.Timeout:
        logger.error("[API] Timeout na requisicao")
        return {
            "status": "erro",
            "mensagem": "Timeout ao cadastrar veiculo - tente novamente"
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"[API] Erro na requisicao: {str(e)}", exc_info=True)
        return {
            "status": "erro",
            "mensagem": f"Erro ao conectar com API: {str(e)}"
        }

    except Exception as e:
        logger.error(f"[ERRO] Erro inesperado: {str(e)}", exc_info=True)
        return {
            "status": "erro",
            "mensagem": f"Erro inesperado: {str(e)}"
        }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Handler principal do Lambda para action group de cadastro de veiculo

    Input: event (dict) - Evento do Bedrock Agent com parametros e sessao
           context (Any) - Contexto do Lambda
    Output: (dict) - Resposta formatada para Bedrock Agent
    """
    logger.info(f"[HANDLER] Event: {json.dumps(event, ensure_ascii=False)}")
    logger.info("[HANDLER] Iniciando action group - Cadastrar Veiculo")

    action_group = event.get('actionGroup', 'CadastrarVeiculo')
    function_name = event.get('function', '')

    try:
        parameters = {p.get('name'): p.get('value') for p in event.get('parameters', [])}
        session_attributes = event.get('sessionAttributes', {})

        logger.info(f"[HANDLER] Funcao: {function_name}")
        logger.info(f"[HANDLER] Atributos de sessao disponiveis: {list(session_attributes.keys())}")

        if function_name == 'cadastrar_veiculo':
            resultado = cadastrar_veiculo(parameters, session_attributes)
        else:
            logger.warning(f"[HANDLER] Funcao desconhecida: {function_name}")
            resultado = {
                "status": "erro",
                "mensagem": f"Funcao desconhecida: {function_name}. Use 'cadastrar_veiculo'"
            }

        logger.info(f"[HANDLER] Processamento concluido - Status: {resultado.get('status')}")

    except Exception as e:
        logger.error(f"[ERRO] Excecao critica no handler: {str(e)}", exc_info=True)

        resultado = {
            "status": "erro",
            "mensagem": "Ocorreu um erro ao processar o cadastro do veiculo. Por favor, tente novamente.",
            "detalhe_tecnico": str(e)[:200]
        }

    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": action_group,
            "function": function_name,
            "functionResponse": {
                "responseBody": {
                    "TEXT": {
                        "body": json.dumps(resultado, ensure_ascii=False)
                    }
                }
            }
        }
    }
