"""
Lambda para envio de mensagens template para motoristas individuais

Input: Dados de motoristas e ofertas de carga
Output: Template enviado, WebSocket subscrito, contexto salvo no chatbot
"""

import json
import os
import logging
import urllib3
import boto3
import sys
from typing import Dict, Any
import datetime

from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../chatbot/action-groups'))
from api_error_logger import log_api_error
from api_retry_util import retry_on_timeout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ecs_ip_resolver import get_websocket_url_with_fallback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../auxiliares'))
from message_history import save_template_message

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm_client = boto3.client('ssm')
dynamodb = boto3.resource('dynamodb')

PARAMETER_STORE_TOKEN_NAME = os.environ.get(
    'PARAMETER_STORE_TOKEN_NAME',
    '/rodosafra/auth/token'
)

NEGOCIACAO_TABLE = os.environ.get('NEGOCIACAO_TABLE', 'negociacao')
negociacao_table = dynamodb.Table(NEGOCIACAO_TABLE)

auth_cookie_cache = None

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
http = urllib3.PoolManager()

lambda_client = boto3.client('lambda')

def obter_token_do_parameter_store():
    """
    Obtem token de autenticacao do Parameter Store

    Input: Nenhum (usa variaveis de ambiente)
    Output: (bool, str, str) - Tupla com sucesso, token e mensagem de erro
    """
    global auth_cookie_cache

    if auth_cookie_cache:
        logger.info("[TOKEN] Usando token em cache")
        return True, auth_cookie_cache, None

    logger.info(f"[TOKEN] Buscando token no Parameter Store: {PARAMETER_STORE_TOKEN_NAME}")

    try:
        response = ssm_client.get_parameter(
            Name=PARAMETER_STORE_TOKEN_NAME,
            WithDecryption=True
        )

        token = response['Parameter']['Value']

        logger.info(f"[TOKEN] {token}")

        if not token:
            return False, None, "Token vazio no Parameter Store"

        auth_cookie_cache = token
        logger.info("[TOKEN] Token obtido com sucesso do Parameter Store")

        return True, token, None

    except ClientError as e:
        error_code = e.response['Error']['Code']

        if error_code == 'ParameterNotFound':
            logger.error(f"[TOKEN] Token nao encontrado no Parameter Store: {PARAMETER_STORE_TOKEN_NAME}")
            return False, None, "Token nao encontrado no Parameter Store"

        elif error_code == 'AccessDeniedException':
            logger.error("[TOKEN] Sem permissao para acessar Parameter Store")
            return False, None, "Sem permissao para acessar token"

        else:
            logger.error(f"[TOKEN] Erro ao acessar Parameter Store: {error_code}")
            return False, None, f"Erro ao obter token: {error_code}"

    except Exception as e:
        logger.error(f"[TOKEN] Erro inesperado ao obter token: {str(e)}")
        return False, None, f"Erro inesperado: {str(e)}"

# ==================== NEGOCIACAO TABLE ====================

def salvar_carga_id_na_negociacao(telefone: str, carga_id: int) -> bool:
    """
    Salva carga_id na tabela negociacao para a sessao mais recente ou cria nova

    Input: telefone (str), carga_id (int)
    Output: (bool) - True se salvou com sucesso, False caso contrario
    """
    if not telefone or not carga_id:
        logger.error(f"[SAVE-CARGA-ID] Parametros invalidos: telefone={telefone}, carga_id={carga_id}")
        return False

    try:
        from boto3.dynamodb.conditions import Key
        from decimal import Decimal

        logger.info(f"[SAVE-CARGA-ID] Salvando carga_id {carga_id} para telefone {telefone}")

        response = negociacao_table.query(
            KeyConditionExpression=Key('telefone').eq(telefone),
            ScanIndexForward=False,
            Limit=1,
            ProjectionExpression='tempo_sessao, session_id, negociacao_iniciada_em'
        )

        items = response.get('Items', [])

        if items:
            tempo_sessao = items[0]['tempo_sessao']
            session_id = items[0].get('session_id')
            logger.info(f"[SAVE-CARGA-ID] Atualizando sessao existente:")
            logger.info(f"[SAVE-CARGA-ID] tempo_sessao: {tempo_sessao}")
            logger.info(f"[SAVE-CARGA-ID] session_id: {session_id}")

            negociacao_table.update_item(
                Key={
                    'telefone': telefone,
                    'tempo_sessao': tempo_sessao
                },
                UpdateExpression='SET carga_id = :cid',
                ExpressionAttributeValues={
                    ':cid': Decimal(str(carga_id))
                }
            )

            logger.info(f"[SAVE-CARGA-ID] carga_id atualizado com sucesso")
            return True
        else:
            logger.info(f"[SAVE-CARGA-ID] Nenhuma sessao encontrada, criando nova")

            now = datetime.datetime.now(datetime.timezone.utc)
            tempo_sessao = now.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
            session_id = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            negociacao_iniciada_em = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

            negociacao_table.put_item(
                Item={
                    'telefone': telefone,
                    'tempo_sessao': tempo_sessao,
                    'session_id': session_id,
                    'negociacao_iniciada_em': negociacao_iniciada_em,
                    'carga_id': Decimal(str(carga_id))
                }
            )

            logger.info(f"[SAVE-CARGA-ID] Nova negociacao criada com carga_id")
            logger.info(f"[SAVE-CARGA-ID] tempo_sessao: {tempo_sessao}")
            logger.info(f"[SAVE-CARGA-ID] session_id: {session_id}")
            logger.info(f"[SAVE-CARGA-ID] negociacao_iniciada_em: {negociacao_iniciada_em}")
            return True

    except Exception as e:
        logger.error(f"[SAVE-CARGA-ID] Erro ao salvar carga_id: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

# ==================== CONFIGURATION ====================

def get_config() -> Dict[str, str]:
    """
    Obtem configuracao de variaveis de ambiente e Parameter Store

    Input: Nenhum (usa variaveis de ambiente)
    Output: (dict) - Dicionario com configuracoes incluindo jwt_token
    """
    sucesso, token, erro = obter_token_do_parameter_store()

    if not sucesso:
        logger.error(f"[CONFIG] Falha ao obter token do Parameter Store: {erro}")
        token = None

    return {
        'api_url': os.environ.get('API_BASE_URL'),
        'jwt_token': token,
        'usuario_responsavel': os.environ.get('USUARIO_RESPONSAVEL', 'assistente.virtual'),
        'template_nome': os.environ.get('TEMPLATE_NOME', 'enviar_oferta_carga_motorista'),
        'chatbot_lambda_name': os.environ.get('CHATBOT_LAMBDA_NAME', 'chatbot')
    }

def get_headers(jwt_token: str) -> Dict[str, str]:
    """
    Constroi headers HTTP com autenticacao JWT

    Input: jwt_token (str)
    Output: (dict) - Dicionario com headers HTTP
    """
    return {
        'Content-Type': 'application/json',
        'Cookie': f"{jwt_token}"
    }

# ==================== PHONE NORMALIZATION ====================

def normalizar_telefone(telefone: str) -> str:
    """
    Normaliza telefone para sempre ter o prefixo 55 (codigo do Brasil)

    Input: telefone (str) - Numero em qualquer formato
    Output: (str) - Telefone normalizado com 13 digitos (55 + DDD + numero)
    """
    if not telefone:
        logger.warning("[NORMALIZE] Telefone vazio recebido para normalizacao")
        return telefone

    telefone_limpo = ''.join(filter(str.isdigit, str(telefone)))

    logger.info(f"[NORMALIZE] Normalizando telefone: '{telefone}' -> '{telefone_limpo}' ({len(telefone_limpo)} digitos)")

    if telefone_limpo.startswith('55') and len(telefone_limpo) == 13:
        logger.info(f"[NORMALIZE] Telefone ja normalizado: {telefone_limpo}")
        return telefone_limpo

    if len(telefone_limpo) == 11:
        telefone_normalizado = f"55{telefone_limpo}"
        logger.info(f"[NORMALIZE] Telefone normalizado de 11 para 13 digitos: {telefone_normalizado}")
        return telefone_normalizado

    if len(telefone_limpo) == 13 and not telefone_limpo.startswith('55'):
        logger.warning(f"[NORMALIZE] Telefone com 13 digitos mas nao comeca com 55: {telefone_limpo}")
        return telefone_limpo

    if len(telefone_limpo) > 13:
        logger.warning(f"[NORMALIZE] Telefone com mais de 13 digitos: {telefone_limpo} - removendo excesso")
        if telefone_limpo.endswith(telefone_limpo[-13:]) and telefone_limpo[-13:-11] == '55':
            return telefone_limpo[-13:]
        return telefone_limpo

    if len(telefone_limpo) < 11:
        logger.error(f"[NORMALIZE] Telefone com menos de 11 digitos apos limpeza: {telefone_limpo} (original: {telefone})")
        return telefone_limpo

    logger.warning(f"[NORMALIZE] Telefone nao se encaixa em nenhum padrao conhecido: {telefone_limpo}")
    return telefone_limpo

# ==================== SEND TEMPLATE ====================

def send_template_message_batch(api_url: str, jwt_token: str, lista_telefones: list,
                                template_nome: str, lista_parametros: list) -> Dict[str, Any]:
    """
    Envia mensagem template para multiplos motoristas em uma unica chamada API

    Input: api_url (str), jwt_token (str), lista_telefones (list), template_nome (str), lista_parametros (list)
    Output: (dict) - Dicionario com status de sucesso e resultados por telefone
    """
    logger.info(f"[BATCH-TEMPLATE] Enviando para {len(lista_telefones)} telefones")
    logger.info(f"[BATCH-TEMPLATE] Template: {template_nome}")
    logger.info(f"[BATCH-TEMPLATE] Telefones: {lista_telefones}")
    logger.info(f"[BATCH-TEMPLATE] Quantidade de parametros: {len(lista_parametros)}")
    
    try:
        url = f"{api_url}/api/chat/conversa/mensagem/template"
        
        payload = {
            "listaTelefones": lista_telefones,
            "template": template_nome,
            "usuario": "assistente.virtual",
            "listaParametros": lista_parametros
        }

        logger.info(f"[BATCH-TEMPLATE] Payload: {payload}")
        logger.info(f"[API-REQUEST] POST {url}")
        logger.info(f"[API-REQUEST] Telefones: {len(lista_telefones)}, Template: {template_nome}")

        response = retry_on_timeout(
            lambda: http.request(
                'POST',
                url,
                body=json.dumps(payload).encode('utf-8'),
                headers=get_headers(jwt_token),
                timeout=30.0
            ),
            max_retries=3,
            operation_name="Send template batch"
        )

        logger.info(f"[BATCH-TEMPLATE] Status {response.status}")
        logger.info(f"[API-RESPONSE] Status: {response.status}, URL: {url}")

        if response.status in [200, 201]:
            try:
                response_body = response.data.decode('utf-8')
                logger.info(f"[API-RESPONSE] Body: {response_body}")

                if not response_body or response_body.strip() == '':
                    logger.info(f"[BATCH-TEMPLATE] Resposta vazia, mas status {response.status} indica sucesso - enviado para {len(lista_telefones)} telefones")
                    return {
                        'success': True,
                        'phones_count': len(lista_telefones),
                        'phones': lista_telefones,
                        'status': response.status
                    }

                data = json.loads(response_body)
                logger.info(f"[BATCH-TEMPLATE] Sucesso - enviado para {len(lista_telefones)} telefones")
                logger.info(f"[API-SUCCESS] Template batch enviado com sucesso para {len(lista_telefones)} telefones")
                return {
                    'success': True,
                    'phones_count': len(lista_telefones),
                    'phones': lista_telefones,
                    'status': response.status,
                    'data': data
                }
            except json.JSONDecodeError as parse_error:
                logger.warning(f"[API-WARNING] Resposta nao e JSON valido: {str(parse_error)}, Body: '{response_body[:200] if 'response_body' in locals() else 'N/A'}', mas requisicao bem-sucedida")
                return {
                    'success': True,
                    'phones_count': len(lista_telefones),
                    'phones': lista_telefones,
                    'status': response.status
                }
        else:
            error_msg = f"Falha com status {response.status}"
            error_body = None
            try:
                error_data = json.loads(response.data.decode('utf-8'))
                error_msg = error_data.get('message', error_msg)
                error_body = error_data
                logger.error(f"[BATCH-TEMPLATE] Erro: {error_msg}")
                logger.error(f"[API-ERROR] POST {url} - Status: {response.status}")
                logger.error(f"[API-ERROR] Erro: {error_msg}")
                logger.error(f"[API-ERROR] Response body: {json.dumps(error_data)[:500]}")
            except:
                logger.error(f"[BATCH-TEMPLATE] Erro: {error_msg}")
                logger.error(f"[API-ERROR] POST {url} - Status: {response.status}")
                logger.error(f"[API-ERROR] Raw response: {response.data.decode('utf-8', errors='replace')[:500]}")

            if response.status >= 500:
                log_api_error(
                    api_route="/api/chat/conversa/mensagem/template",
                    error_code=response.status,
                    error_message=f"Erro ao enviar template em batch ({response.status})",
                    payload={"phones_count": len(lista_telefones), "template": template_nome},
                    response_body=response.data.decode('utf-8', errors='replace')[:1000]
                )

            return {
                'success': False,
                'phones_count': len(lista_telefones),
                'phones': lista_telefones,
                'status': response.status,
                'error': error_msg
            }

    except Exception as e:
        logger.error(f"[BATCH-TEMPLATE] Excecao: {str(e)}")
        logger.error(f"[API-EXCEPTION] POST {url if 'url' in locals() else 'N/A'} - Exception: {type(e).__name__}: {str(e)}")
        import traceback
        logger.error(f"[API-EXCEPTION] Traceback: {traceback.format_exc()[:500]}")
        return {
            'success': False,
            'phones_count': len(lista_telefones),
            'phones': lista_telefones,
            'error': str(e)
        }

def process_post_template_steps_batch(api_url: str, jwt_token: str, telefones: list,
                                      chatbot_lambda_name: str, template_messages: dict,
                                      motoristas_info: dict,
                                      session_attributes_por_telefone: dict) -> Dict[str, Any]:
    """
    Processa etapas pos-template para batch de motoristas

    Input: api_url (str), jwt_token (str), telefones (list), chatbot_lambda_name (str), template_messages (dict), motoristas_info (dict), session_attributes_por_telefone (dict)
    Output: (dict) - Dicionario mapeando telefone para resultado
    """
    results = {}

    for telefone in telefones:
        template_message = template_messages.get(telefone, '')
        motorista_info = motoristas_info.get(telefone, {})
        session_attrs = session_attributes_por_telefone.get(telefone, {})

        context_message = build_chatbot_context_message_dual(
            template_message,
            motorista_info.get('cadastrado_telefone', False),
            motorista_info.get('cadastrado_cpf', False),
            session_attrs
        )

        logger.info(f"[POST-STEPS] Processando motorista {telefone}")
        logger.info(f"[POST-STEPS] Nome: {motorista_info.get('nome')}")
        logger.info(f"[POST-STEPS] Telefone cadastrado: {motorista_info.get('cadastrado_telefone')}")
        logger.info(f"[POST-STEPS] CPF cadastrado: {motorista_info.get('cadastrado_cpf')}")
        logger.info(f"[POST-STEPS] Session attributes count: {len(session_attrs)}")

        driver_result = process_driver(
            api_url,
            jwt_token,
            telefone,
            chatbot_lambda_name,
            context_message,
            motorista_info,
            session_attrs
        )

        results[telefone] = driver_result

    return results

def send_template_message(api_url: str, jwt_token: str, telefone: str,
                         template_nome: str, parametros: list) -> Dict[str, Any]:
    """
    Envia mensagem template para um motorista individual

    Input: api_url (str), jwt_token (str), telefone (str), template_nome (str), parametros (list)
    Output: (dict) - Dicionario com status de sucesso e detalhes
    """
    logger.info(f"[TEMPLATE] Enviando para {telefone}")
    logger.info(f"[TEMPLATE] Template: {template_nome}")
    logger.info(f"[TEMPLATE] Parametros: {parametros}")
    
    try:
        url = f"{api_url}/api/chat/conversa/mensagem/template"
        
        payload = {
            "telefone": telefone,
            "template": template_nome,
            "usuario": "assistente.virtual",
            "parametros": parametros
        }

        logger.info(f"[TEMPLATE] Payload: {payload}")
        logger.info(f"[API-REQUEST] POST {url}")
        logger.info(f"[API-REQUEST] Telefone: {telefone}, Template: {template_nome}")

        response = retry_on_timeout(
            lambda: http.request(
                'POST',
                url,
                body=json.dumps(payload).encode('utf-8'),
                headers=get_headers(jwt_token),
                timeout=15.0
            ),
            max_retries=3,
            operation_name=f"Send template to {telefone}",
            telefone=telefone
        )

        logger.info(f"[TEMPLATE] Status {response.status} para {telefone}")
        logger.info(f"[API-RESPONSE] Status: {response.status}, Telefone: {telefone}")

        if response.status in [200, 201]:
            try:
                response_body = response.data.decode('utf-8')
                logger.info(f"[API-RESPONSE] Body: {response_body}")

                if not response_body or response_body.strip() == '':
                    logger.info(f"[TEMPLATE] Resposta vazia, mas status {response.status} indica sucesso para {telefone}")
                    return {
                        'success': True,
                        'telefone': telefone,
                        'status': response.status
                    }

                data = json.loads(response_body)
                logger.info(f"[TEMPLATE] Sucesso para {telefone}")
                logger.info(f"[API-SUCCESS] Template enviado com sucesso para {telefone}")
                return {
                    'success': True,
                    'telefone': telefone,
                    'status': response.status,
                    'data': data
                }
            except json.JSONDecodeError as parse_error:
                logger.warning(f"[API-WARNING] Resposta nao e JSON valido: {str(parse_error)}, Body: '{response_body[:200] if 'response_body' in locals() else 'N/A'}', mas requisicao bem-sucedida")
                return {
                    'success': True,
                    'telefone': telefone,
                    'status': response.status
                }
        else:
            error_msg = f"Falha com status {response.status}"
            try:
                error_data = json.loads(response.data.decode('utf-8'))
                error_msg = error_data.get('message', error_msg)
                logger.error(f"[TEMPLATE] Erro para {telefone}: {error_msg}")
                logger.error(f"[API-ERROR] POST {url} - Status: {response.status}, Telefone: {telefone}")
                logger.error(f"[API-ERROR] Erro: {error_msg}")
                logger.error(f"[API-ERROR] Response body: {json.dumps(error_data)[:500]}")
            except:
                logger.error(f"[TEMPLATE] Erro para {telefone}: {error_msg}")
                logger.error(f"[API-ERROR] POST {url} - Status: {response.status}, Telefone: {telefone}")
                logger.error(f"[API-ERROR] Raw response: {response.data.decode('utf-8', errors='replace')[:500]}")

            if response.status >= 500:
                log_api_error(
                    api_route="/api/chat/conversa/mensagem/template",
                    error_code=response.status,
                    error_message=f"Erro ao enviar template ({response.status})",
                    payload={"telefone": f"***{telefone[-4:]}", "template": template_nome},
                    response_body=response.data.decode('utf-8', errors='replace')[:1000]
                )

            return {
                'success': False,
                'telefone': telefone,
                'status': response.status,
                'error': error_msg
            }

    except Exception as e:
        logger.error(f"[TEMPLATE] Excecao para {telefone}: {str(e)}")
        logger.error(f"[API-EXCEPTION] POST {url if 'url' in locals() else 'N/A'} - Exception: {type(e).__name__}: {str(e)}")
        import traceback
        logger.error(f"[API-EXCEPTION] Traceback: {traceback.format_exc()[:500]}")
        return {
            'success': False,
            'telefone': telefone,
            'error': str(e)
        }

# ==================== ASSUME CONVERSATION ====================

def assumir_conversa(api_url: str, jwt_token: str, cod_conversa: str,
                    usuario: str) -> Dict[str, Any]:
    """
    Assume conversa apos template ser enviado

    Input: api_url (str), jwt_token (str), cod_conversa (str), usuario (str)
    Output: (dict) - Dicionario com status de sucesso e detalhes
    """
    logger.info(f"[ASSUME] Assumindo conversa {cod_conversa}")

    try:
        url = f"{api_url}/api/chat/conversa/visualizar-mensagens"

        payload = {
            "codConversa": cod_conversa,
            "usuarioResponsavel": usuario
        }

        logger.info(f"[API-REQUEST] PUT {url}")
        logger.info(f"[API-REQUEST] Conversa: {cod_conversa}, Usuario: {usuario}")

        response = retry_on_timeout(
            lambda: http.request(
                'PUT',
                url,
                body=json.dumps(payload).encode('utf-8'),
                headers=get_headers(jwt_token),
                timeout=15.0
            ),
            max_retries=3,
            operation_name=f"Assumir conversa {cod_conversa}",
            telefone=cod_conversa
        )

        logger.info(f"[ASSUME] Status {response.status} para {cod_conversa}")
        logger.info(f"[API-RESPONSE] Status: {response.status}, Conversa: {cod_conversa}")

        if response.status in [200, 204]:
            logger.info(f"[ASSUME] Sucesso para {cod_conversa}")
            logger.info(f"[API-SUCCESS] Conversa assumida com sucesso: {cod_conversa}")
            return {
                'success': True,
                'cod_conversa': cod_conversa,
                'status': response.status
            }
        else:
            error_msg = f"Falha com status {response.status}"
            try:
                error_data = json.loads(response.data.decode('utf-8'))
                error_msg = error_data.get('message', error_msg)
                logger.error(f"[ASSUME] Erro para {cod_conversa}: {error_msg}")
                logger.error(f"[API-ERROR] PUT {url} - Status: {response.status}")
                logger.error(f"[API-ERROR] Erro: {error_msg}")
                logger.error(f"[API-ERROR] Response body: {json.dumps(error_data)[:500]}")
            except:
                logger.error(f"[ASSUME] Erro para {cod_conversa}: {error_msg}")
                logger.error(f"[API-ERROR] PUT {url} - Status: {response.status}")
                logger.error(f"[API-ERROR] Raw response: {response.data.decode('utf-8', errors='replace')[:500]}")

            return {
                'success': False,
                'cod_conversa': cod_conversa,
                'status': response.status,
                'error': error_msg
            }

    except Exception as e:
        logger.error(f"[ASSUME] Excecao para {cod_conversa}: {str(e)}")
        logger.error(f"[API-EXCEPTION] PUT {url if 'url' in locals() else 'N/A'} - Exception: {type(e).__name__}: {str(e)}")
        import traceback
        logger.error(f"[API-EXCEPTION] Traceback: {traceback.format_exc()[:500]}")
        return {
            'success': False,
            'cod_conversa': cod_conversa,
            'error': str(e)
        }

# ==================== WEBSOCKET SUBSCRIPTION ====================

def subscribe_to_websocket(telefone: str) -> Dict[str, Any]:
    """
    Inscreve no topico WebSocket para conversa do motorista

    Input: telefone (str)
    Output: (dict) - Dicionario com status de sucesso e detalhes
    """
    logger.info(f"[WEBSOCKET] Inscrevendo no topico para {telefone}")

    try:
        ecs_url = get_websocket_url_with_fallback()

        if not ecs_url:
            logger_msg = "Nao foi possivel resolver URL do WebSocket ECS"
            logger.warning(f"[WEBSOCKET] {logger_msg}")
            return {
                'success': False,
                'telefone': telefone,
                'error': logger_msg,
                'skipped': True
            }

        logger.info(f"[WEBSOCKET] Usando WebSocket URL: {ecs_url}")
        url = f"{ecs_url}/subscribe"

        payload = {
            "codConversa": telefone
        }

        logger.info(f"[API-REQUEST] POST {url}")
        logger.info(f"[API-REQUEST] Inscrevendo telefone: {telefone}")

        response = retry_on_timeout(
            lambda: http.request(
                'POST',
                url,
                body=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
                timeout=15.0
            ),
            max_retries=3,
            operation_name=f"Subscribe websocket {telefone}",
            telefone=telefone
        )

        logger.info(f"[WEBSOCKET] Status da inscricao {response.status} para {telefone}")
        logger.info(f"[API-RESPONSE] Status: {response.status}, Telefone: {telefone}")

        if response.status in [200, 201]:
            try:
                response_body = response.data.decode('utf-8')
                logger.info(f"[API-RESPONSE] Body: {response_body}")

                if not response_body or response_body.strip() == '':
                    logger.info(f"[WEBSOCKET] Resposta vazia, mas status {response.status} indica sucesso para {telefone}")
                    return {
                        'success': True,
                        'telefone': telefone,
                        'status': response.status
                    }

                data = json.loads(response_body)
                logger.info(f"[WEBSOCKET] Sucesso - inscrito em {telefone}")
                logger.info(f"[API-SUCCESS] Inscricao WebSocket bem-sucedida para {telefone}")
                return {
                    'success': True,
                    'telefone': telefone,
                    'status': response.status,
                    'data': data
                }
            except json.JSONDecodeError as parse_error:
                logger.warning(f"[API-WARNING] Resposta nao e JSON valido: {str(parse_error)}, Body: '{response_body[:200] if 'response_body' in locals() else 'N/A'}', mas requisicao bem-sucedida")
                return {
                    'success': True,
                    'telefone': telefone,
                    'status': response.status
                }
        else:
            error_msg = f"Falha com status {response.status}"
            error_data = None
            try:
                error_data = json.loads(response.data.decode('utf-8'))
                error_msg = error_data.get('message', error_msg)
            except:
                pass

            logger.error(f"[WEBSOCKET] Erro para {telefone}: {error_msg}")
            logger.error(f"[API-ERROR] POST {ecs_url}/subscribe - Status: {response.status}")
            logger.error(f"[API-ERROR] Erro: {error_msg}")
            if error_data:
                logger.error(f"[API-ERROR] Response body: {json.dumps(error_data)[:500]}")
            return {
                'success': False,
                'telefone': telefone,
                'status': response.status,
                'error': error_msg
            }

    except Exception as e:
        logger.error(f"[WEBSOCKET] Excecao para {telefone}: {str(e)}")
        logger.error(f"[API-EXCEPTION] POST {ecs_url}/subscribe - Exception: {type(e).__name__}: {str(e)}")
        logger.error(f"[API-EXCEPTION] Traceback: {traceback.format_exc()[:500]}")
        return {
            'success': False,
            'telefone': telefone,
            'error': str(e)
        }

# ==================== CHATBOT INTEGRATION ====================

def invoke_chatbot_for_context(chatbot_lambda_name: str, telefone: str,
                               template_message: str, motorista_data: dict,
                               session_attributes: dict = None) -> Dict[str, Any]:
    """
    Invoca Lambda do chatbot com contexto incluindo flags de cadastro e session attributes

    Input: chatbot_lambda_name (str), telefone (str), template_message (str), motorista_data (dict), session_attributes (dict opcional)
    Output: (dict) - Dicionario com status de sucesso e detalhes
    """
    logger.info(f"[CHATBOT] Invocando chatbot com flags de cadastro para {telefone}")

    try:
        cadastrado_telefone = motorista_data.get('cadastrado_telefone', False)
        cadastrado_cpf = motorista_data.get('cadastrado_cpf', False)

        context_message = build_chatbot_context_message_dual(
            template_message,
            cadastrado_telefone,
            cadastrado_cpf,
            session_attributes or {}
        )

        logger.info(f"[CHATBOT] Tamanho da mensagem de contexto: {len(context_message)} caracteres")
        logger.info(f"[CHATBOT] Status de cadastro - Telefone: {cadastrado_telefone}, CPF: {cadastrado_cpf}")

        merged_session_attrs = session_attributes.copy() if session_attributes else {}

        if 'id_motorista' not in merged_session_attrs:
            merged_session_attrs['id_motorista'] = motorista_data.get('id_motorista', '')
        if 'nome' not in merged_session_attrs:
            merged_session_attrs['nome'] = motorista_data.get('nome', 'Motorista')
        if 'telefone' not in merged_session_attrs:
            merged_session_attrs['telefone'] = telefone
        if 'cadastrado_telefone' not in merged_session_attrs:
            merged_session_attrs['cadastrado_telefone'] = cadastrado_telefone
        if 'cadastrado_cpf' not in merged_session_attrs:
            merged_session_attrs['cadastrado_cpf'] = cadastrado_cpf
        if 'timestamp' not in merged_session_attrs:
            merged_session_attrs['timestamp'] = datetime.datetime.utcnow().isoformat() + 'Z'

        if 'data_atual' not in merged_session_attrs:
            agora_utc = datetime.datetime.utcnow()
            dias_semana = ['Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira',
                          'Sexta-feira', 'Sábado', 'Domingo']
            dia_semana_nome = dias_semana[agora_utc.weekday()]
            merged_session_attrs['data_atual'] = agora_utc.strftime('%Y-%m-%d %H:%M:%S UTC')
            merged_session_attrs['dia_semana'] = dia_semana_nome

        logger.info(f"[CHATBOT] Quantidade de session attributes: {len(merged_session_attrs)}")

        payload = {
            "mensagem": {
                "texto": context_message,
                "codConversa": telefone,
                "codMensagem": f"template_{telefone}"
            },
            "sessionAttributes": merged_session_attrs
        }

        logger.info(f"[CHATBOT] Enviando template com FLUXO ATIVO e contexto de cadastro")
        logger.info(f"[CHATBOT] Payload: {payload}")

        response = lambda_client.invoke(
            FunctionName=chatbot_lambda_name,
            InvocationType='RequestResponse',
            Payload=json.dumps(payload, ensure_ascii=False).encode('utf-8')
        )

        status_code = response.get('StatusCode', 0)
        logger.info(f"[CHATBOT] Status da invocacao: {status_code}")

        if status_code in [200, 202]:
            try:
                response_body = response['Payload'].read().decode('utf-8')
                logger.info(f"[CHATBOT] Response body: {response_body}")

                if not response_body or response_body.strip() == '':
                    logger.info(f"[CHATBOT] Resposta vazia, mas status {status_code} indica sucesso para {telefone}")
                    return {
                        'success': True,
                        'telefone': telefone,
                        'status_code': status_code,
                        'session_attributes_sent': session_attributes,
                        'context_type': 'FLUXO_ATIVO_DUAL_CADASTRO',
                        'note': 'Resposta vazia mas invocacao bem-sucedida'
                    }

                response_payload = json.loads(response_body)
                logger.info(f"[CHATBOT] Sucesso - contexto salvo para {telefone} com flags de cadastro")
                return {
                    'success': True,
                    'telefone': telefone,
                    'status_code': status_code,
                    'response': response_payload,
                    'session_attributes_sent': session_attributes,
                    'context_type': 'FLUXO_ATIVO_DUAL_CADASTRO'
                }
            except json.JSONDecodeError as parse_error:
                logger.warning(f"[CHATBOT] Resposta nao e JSON valido: {str(parse_error)}, Body: '{response_body[:200] if 'response_body' in locals() else 'N/A'}'")
                return {
                    'success': True,
                    'telefone': telefone,
                    'status_code': status_code,
                    'session_attributes_sent': session_attributes,
                    'context_type': 'FLUXO_ATIVO_DUAL_CADASTRO',
                    'note': 'Resposta nao pode ser processada mas invocacao bem-sucedida'
                }
        else:
            error_msg = f"Invocacao falhou com status code {status_code}"
            logger.error(f"[CHATBOT] Erro para {telefone}: {error_msg}")
            return {
                'success': False,
                'telefone': telefone,
                'status_code': status_code,
                'error': error_msg
            }

    except Exception as e:
        logger.error(f"[CHATBOT] Excecao para {telefone}: {str(e)}")
        return {
            'success': False,
            'telefone': telefone,
            'error': str(e)
        }

# ==================== PARAMETER FORMATTING ====================

def process_driver(api_url: str, jwt_token: str, telefone: str,
                  chatbot_lambda_name: str, context_message: str,
                  motorista_info: dict, session_attributes: dict = None) -> Dict[str, Any]:
    """
    Processa motorista individual inscrevendo no websocket e invocando chatbot

    Input: api_url (str), jwt_token (str), telefone (str), chatbot_lambda_name (str), context_message (str), motorista_info (dict), session_attributes (dict opcional)
    Output: (dict) - Dicionario com status de sucesso e detalhes
    """
    result = {
        'telefone': telefone,
        'success': False,
        'nome': motorista_info.get('nome', 'Motorista'),
        'id_motorista': motorista_info.get('id_motorista', ''),
        'steps': {}
    }

    try:
        logger.info(f"[DRIVER] {telefone} Etapa 1: Inscrevendo no WebSocket")
        websocket_result = subscribe_to_websocket(telefone)
        result['steps']['subscribe_websocket'] = websocket_result

        if not websocket_result.get('success'):
            if websocket_result.get('skipped'):
                logger.warning(f"[DRIVER] {telefone} Inscricao WebSocket pulada (nao foi possivel resolver URL)")
            else:
                logger.warning(f"[DRIVER] {telefone} Inscricao WebSocket falhou, mas continuando")
        else:
            logger.info(f"[DRIVER] {telefone} Inscricao WebSocket bem-sucedida")

        logger.info(f"[DRIVER] {telefone} Etapa 2: Invocando chatbot")
        logger.info(f"[DRIVER] {telefone} Quantidade de session attributes: {len(session_attributes) if session_attributes else 0}")

        chatbot_payload = {
            'mensagem': {
                'texto': context_message,
                'codConversa': telefone,
                'telefone': telefone
            },
            'sessionAttributes': session_attributes or {}
        }

        logger.info(f"[DRIVER] {telefone} Chaves do payload do chatbot: {list(chatbot_payload.keys())}")
        logger.info(f"[DRIVER] {telefone} Chaves dos session attributes: {list(chatbot_payload['sessionAttributes'].keys())}")

        chatbot_response = lambda_client.invoke(
            FunctionName=chatbot_lambda_name,
            InvocationType='RequestResponse',
            Payload=json.dumps(chatbot_payload, ensure_ascii=False)
        )

        chatbot_status = chatbot_response.get('StatusCode', 500)

        result['steps']['invoke_chatbot'] = {
            'status_code': chatbot_status,
            'success': (chatbot_status == 200)
        }

        if chatbot_status == 200:
            logger.info(f"[DRIVER] {telefone} Chatbot invocado com sucesso")

            carga_id_to_save = session_attributes.get('carga_id') if session_attributes else None
            if carga_id_to_save and telefone:
                logger.info(f"[DRIVER] {telefone} Salvando carga_id {carga_id_to_save} na negociacao")
                save_result = salvar_carga_id_na_negociacao(telefone, carga_id_to_save)
                result['steps']['save_carga_id'] = {
                    'success': save_result,
                    'carga_id': carga_id_to_save
                }
            else:
                logger.warning(f"[DRIVER] {telefone} carga_id nao disponivel para salvar (carga_id={carga_id_to_save})")

            logger.info(f"[DRIVER] {telefone} Salvando template message no historico")
            template_save_result = save_template_message(
                telefone=telefone,
                template_text=context_message,
                tempo_sessao=None,
                negociacao_iniciada_em=session_attributes.get('timestamp') if session_attributes else None,
                table_name=NEGOCIACAO_TABLE
            )
            result['steps']['save_template_history'] = {
                'success': template_save_result
            }
            if template_save_result:
                logger.info(f"[DRIVER] {telefone} Template message salva no historico com sucesso")
            else:
                logger.warning(f"[DRIVER] {telefone} Falha ao salvar template message no historico")

            result['success'] = True
        else:
            logger.error(f"[DRIVER] {telefone} Invocacao do chatbot falhou: {chatbot_status}")

        return result

    except Exception as e:
        logger.error(f"[DRIVER] {telefone} Excecao em process_driver: {str(e)}")
        result['steps']['exception'] = str(e)
        return result

def format_location(endereco: dict) -> str:
    """
    Formata localizacao como Cidade - Estado

    Input: endereco (dict)
    Output: (str) - String formatada com cidade e estado
    """
    cidade = endereco.get('cidade', 'N/A')
    estado = endereco.get('uf', 'N/A')
    estado = estado.upper() if len(estado) == 2 else estado
    return f"{cidade} - {estado}"

def format_valor_frete(oferta_data: dict) -> str:
    """
    Formata valor do frete como R$ valor/unidade

    Input: oferta_data (dict)
    Output: (str) - String formatada com valor do frete
    """
    valor = oferta_data.get('valor_frete', 0)
    unidade = oferta_data.get('unidade_medida', 'ton')

    valor_formatado = f"{float(valor):.2f}"

    return f"R$ {valor_formatado}/{unidade}"

def extract_first_name(nome_completo: str) -> str:
    """
    Extrai primeiro nome do nome completo

    Input: nome_completo (str)
    Output: (str) - Primeiro nome ou "Motorista" se vazio
    """
    if not nome_completo:
        return "Motorista"

    return nome_completo.strip().split()[0]

def build_template_parameters(motorista_data: dict, oferta_data: dict) -> list:
    """
    Constroi os parametros do template a partir dos dados do motorista e da oferta.
    Inputs: motorista_data (dict), oferta_data (dict)
    Outputs: lista [nome_motorista, cidade_origem, cidade_destino, material, frete_formatado]
    """
    nome_completo = motorista_data.get('nome', 'Motorista')
    nome_motorista = extract_first_name(nome_completo)

    origem = oferta_data.get('origem', {})
    endereco_origem = origem.get('endereco', {})
    cidade_origem = endereco_origem.get('cidade', 'Origem')

    destino = oferta_data.get('destino', {})
    endereco_destino = destino.get('endereco', {})
    cidade_destino = endereco_destino.get('cidade', 'Destino')

    material = oferta_data.get('material', 'Carga')

    frete_valor = oferta_data.get('frete_motorista')
    if not frete_valor and frete_valor != 0:
        frete_valor = oferta_data.get('valor_frete', 0)

    try:
        if frete_valor == '' or frete_valor is None:
            frete_valor_num = 0.0
        else:
            frete_valor_num = float(frete_valor)
    except (ValueError, TypeError) as e:
        logger.warning(f"[AVISO] Valor de frete invalido: {frete_valor}, erro: {e}, usando 0.0")
        frete_valor_num = 0.0

    logger.debug(f"[DEBUG] Valor de frete convertido: {frete_valor_num}")

    frete_formatado = f"R$ {frete_valor_num:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')

    tipo_pagamento = oferta_data.get('tipo_pagamento_frete', 'fixo')
    if tipo_pagamento == 'tonelada':
        frete_formatado = f"{frete_formatado} por tonelada"
    else:
        frete_formatado = f"{frete_formatado} (valor fixo)"

    logger.debug(f"[DEBUG] Frete formatado final: {frete_formatado}")

    parametros = [
        nome_motorista,
        cidade_origem,
        cidade_destino,
        material,
        frete_formatado
    ]

    return parametros

def build_offer_summary(motorista_data: dict, oferta_data: dict, parametros: list) -> str:
    """
    Gera o resumo da oferta para contexto do chatbot, incluindo campos como carga_id, material, frete_motorista e carga_urgente.
    Inputs: motorista_data (dict), oferta_data (dict), parametros (list existente)
    Outputs: texto resumido da oferta
    """
    summary_parts = []

    tipo_pagamento = oferta_data.get('tipo_pagamento_frete', 'fixo')
    descricao_pagamento = "valor total fixo" if tipo_pagamento == 'fixo' else "valor por tonelada"

    summary_parts.append(
        f"Transporte de {parametros[3]} de {parametros[1]} para {parametros[2]}, "
        f"valor do frete: {parametros[4]} ({descricao_pagamento})"
    )

    if 'carga_id' in oferta_data:
        summary_parts.append(f"ID da carga: {oferta_data['carga_id']}")

    if oferta_data.get('carga_urgente'):
        summary_parts.append("CARGA URGENTE")

    veiculo = oferta_data.get('veiculo', {})
    tipos_veiculo = veiculo.get('tipos', [])
    if tipos_veiculo:
        tipos_str = ', '.join(tipos_veiculo)
        summary_parts.append(f"Tipos de veiculo: {tipos_str}")

    equipamentos = veiculo.get('equipamentos', [])
    if equipamentos:
        equip_str = ', '.join(equipamentos)
        summary_parts.append(f"Equipamentos necessarios: {equip_str}")

    inicio = oferta_data.get('inicio_periodo')
    fim = oferta_data.get('fim_periodo')
    if inicio and fim:
        summary_parts.append(f"Periodo: {inicio} ate {fim}")

    summary = '. '.join(summary_parts)
    if not summary.endswith('.'):
        summary += '.'

    return summary

def build_template_message(parametros: list) -> str:
    """
    Monta a mensagem completa do template enviada ao motorista, seguindo o formato do WhatsApp.
    Inputs: parametros [nome_motorista, cidade_origem, cidade_destino, material, frete_formatado]
    Outputs: texto do template
    """
    nome_motorista = parametros[0]
    cidade_origem = parametros[1]
    cidade_destino = parametros[2]
    material = parametros[3]
    frete_formatado = parametros[4]

    template_message = (
        f"Ola, {nome_motorista}, \n"
        f"Somos da Rodosafra e estamos com um Frete disponivel para carregar de {cidade_origem} "
        f"para {cidade_destino} de {material}.\n"
        f"Valor frete motorista: {frete_formatado}\n"
        f"Aguardamos o seu retorno, obrigado e tenha um bom dia!"
    )

    return template_message

def build_chatbot_context_message_dual(template_message: str, cadastrado_telefone: bool,
                                       cadastrado_cpf: bool, session_attrs: dict = None) -> str:
    """
    Monta a mensagem de contexto para o chatbot, incluindo template enviado, status de cadastro e session attributes.
    Inputs: template_message (str), cadastrado_telefone (bool), cadastrado_cpf (bool), session_attrs (dict opcional)
    Outputs: mensagem de contexto completa para o chatbot
    """

    if cadastrado_telefone and cadastrado_cpf:
        cenario = "CENARIO 1: Telefone e CPF cadastrados - Confirmar identidade do motorista"
    elif not cadastrado_telefone and cadastrado_cpf:
        cenario = "CENARIO 2: CPF cadastrado mas telefone diferente - Problema no cadastro - Transbordar para equipe de cadastro"
    elif cadastrado_telefone and not cadastrado_cpf:
        cenario = "CENARIO 3: Telefone cadastrado mas CPF diferente - Problema no cadastro - Transbordar para equipe de cadastro"
    else:
        cenario = "CENARIO 4: Telefone e CPF nao cadastrados - Iniciar cadastro completo"

    session_info_lines = []
    if session_attrs:
        if session_attrs.get('nome'):
            session_info_lines.append(f"- Nome: {session_attrs.get('nome')}")
        if session_attrs.get('telefone'):
            session_info_lines.append(f"- Telefone: {session_attrs.get('telefone')}")
        if session_attrs.get('id_motorista'):
            session_info_lines.append(f"- ID Motorista: {session_attrs.get('id_motorista')}")
        if session_attrs.get('data_atual'):
            session_info_lines.append(f"- Data atual: {session_attrs.get('data_atual')}")
        if session_attrs.get('dia_semana'):
            session_info_lines.append(f"- Dia da semana: {session_attrs.get('dia_semana')}")
        if session_attrs.get('cnh_categoria'):
            session_info_lines.append(f"- CNH Categoria: {session_attrs.get('cnh_categoria')}")
        if session_attrs.get('data_nascimento'):
            session_info_lines.append(f"- Data de nascimento: {session_attrs.get('data_nascimento')}")
        if session_attrs.get('embarque_id'):
            session_info_lines.append(f"- Embarque ID: {session_attrs.get('embarque_id')}")
        if session_attrs.get('embarque_origem'):
            session_info_lines.append(f"- Embarque origem: {session_attrs.get('embarque_origem')}")
        if session_attrs.get('embarque_destino'):
            session_info_lines.append(f"- Embarque destino: {session_attrs.get('embarque_destino')}")
        veiculos_count = session_attrs.get('total_veiculos', session_attrs.get('veiculos_count'))
        if veiculos_count:
            session_info_lines.append(f"- Total de veiculos cadastrados: {veiculos_count}")
        if session_attrs.get('carga_id'):
            session_info_lines.append(f"- Carga ID: {session_attrs.get('carga_id')}")
        if session_attrs.get('carga_urgente'):
            session_info_lines.append(f"- Carga urgente: SIM")
        if session_attrs.get('tipo_pagamento_frete'):
            tipo_pag = session_attrs.get('tipo_pagamento_frete')
            descricao_tipo = "Valor total fixo" if tipo_pag == 'fixo' else "Valor por tonelada"
            session_info_lines.append(f"- Tipo de pagamento do frete: {tipo_pag} ({descricao_tipo})")
        if session_attrs.get('peso_total_programado'):
            peso = session_attrs.get('peso_total_programado')
            tipo_prog = session_attrs.get('tipo_programacao', '')

            if tipo_prog == 'fixa':
                descricao_prog = f"{peso} toneladas (LIMITE MAXIMO - pode carregar menos, mas NAO pode ultrapassar)"
            elif tipo_prog == 'livre':
                descricao_prog = f"{peso} toneladas (PODE ULTRAPASSAR - valor e apenas referencia)"
            else:
                descricao_prog = f"{peso} toneladas"

            session_info_lines.append(f"- Peso total programado: {descricao_prog}")
        elif session_attrs.get('tipo_programacao'):
            # Caso raro: tem tipo_programacao mas nao tem peso
            tipo_prog = session_attrs.get('tipo_programacao')
            session_info_lines.append(f"- Tipo de programacao: {tipo_prog}")

    context_parts = [
        f"O motorista recebeu a seguinte oferta de nosso sistema:",
        f"{template_message}",
        ""
    ]

    if session_info_lines:
        context_parts.append("Informacoes do motorista (session attributes):")
        context_parts.extend(session_info_lines)
        context_parts.append("")

    context_parts.extend([
        "Status de cadastro (Flags True/False):",
        f"- Telefone cadastrado: {cadastrado_telefone}",
        f"- CPF cadastrado: {cadastrado_cpf}",
        f"{cenario}",
        "",
        "Siga os fluxos de acordo com suas instrucoes para FLUXO ATIVO."
    ])

    context = "\n".join(context_parts)

    return context

def lambda_handler(event, context):
    """
    Handler para envio em lote com session attributes completos.
    Inputs: event (dict ou str) com lista de motoristas e dados de oferta; context (objeto Lambda)
    Outputs: resposta HTTP com resultado do envio e passos por motorista
    """
    logger.info(f"[HANDLER] Event: {json.dumps(event if isinstance(event, dict) else json.loads(event), ensure_ascii=False)}")
    logger.info("[INFO] Inicio do envio em lote com session attributes completos")

    if isinstance(event, str):
        event = json.loads(event)

    motoristas = event.get('motoristas', [])
    oferta_data = event.get('oferta', {})
    multi_offer = event.get('multi_offer', False)

    if not motoristas:
        logger.error("[ERRO] Lista de motoristas ausente ou vazia")
        return {
            'statusCode': 400,
            'body': json.dumps({
                'success': False,
                'message': 'motoristas list is required and cannot be empty'
            })
        }

    if not multi_offer and not oferta_data:
        logger.error("[ERRO] Dados de oferta ausentes")
        return {
            'statusCode': 400,
            'body': json.dumps({
                'success': False,
                'message': 'oferta field is required (unless multi_offer=true)'
            })
        }

    config = get_config()

    if not config['api_url'] or not config['jwt_token']:
        logger.error("[ERRO] Configuracao da API ausente ou token indisponivel")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'success': False,
                'message': 'API_BASE_URL not configured or token not available in Parameter Store'
            })
        }

    template_nome = config['template_nome']
    chatbot_lambda_name = config['chatbot_lambda_name']

    logger.info(f"[INFO] Quantidade de motoristas: {len(motoristas)}")
    logger.info(f"[INFO] Template: {template_nome}")
    logger.info(f"[INFO] Lambda do chatbot: {chatbot_lambda_name}")
    logger.info(f"[INFO] Modo multi-offer: {multi_offer}")
    logger.info("[INFO] Uso de session attributes completos")

    lista_telefones = []
    motoristas_info = {}
    lista_parametros = []
    template_messages = {}
    session_attributes_por_telefone = {}

    for motorista in motoristas:
        telefone_raw = motorista.get('telefone')

        if telefone_raw:
            telefone = normalizar_telefone(telefone_raw)

            lista_telefones.append(telefone)

            motoristas_info[telefone] = {
                'id_motorista': motorista.get('id_motorista', ''),
                'nome': motorista.get('nome', 'Motorista'),
                'cadastrado_telefone': motorista.get('cadastrado_telefone', False),
                'cadastrado_cpf': motorista.get('cadastrado_cpf', False)
            }

            session_attrs = motorista.get('session_attributes', {})

            current_oferta_data = motorista.get('oferta', oferta_data) if multi_offer else oferta_data

            # Extrair peso_total_programado e tipo_programacao da oferta e adicionar aos session attributes
            if current_oferta_data.get('peso_total_programado') and 'peso_total_programado' not in session_attrs:
                session_attrs['peso_total_programado'] = current_oferta_data['peso_total_programado']
                logger.info(f"[INFO] Adicionado peso_total_programado aos session attrs: {current_oferta_data['peso_total_programado']}")

            if current_oferta_data.get('tipo_programacao') and 'tipo_programacao' not in session_attrs:
                session_attrs['tipo_programacao'] = current_oferta_data['tipo_programacao']
                logger.info(f"[INFO] Adicionado tipo_programacao aos session attrs: {current_oferta_data['tipo_programacao']}")

            session_attributes_por_telefone[telefone] = session_attrs

            logger.info(f"[INFO] Motorista {telefone} (original: {telefone_raw})")
            logger.info(f"[INFO] Nome: {motorista.get('nome')}")
            logger.info(f"[INFO] Telefone cadastrado: {motorista.get('cadastrado_telefone')}")
            logger.info(f"[INFO] CPF cadastrado: {motorista.get('cadastrado_cpf')}")
            logger.info(f"[INFO] Quantidade de session attributes: {len(session_attrs)}")
            if multi_offer:
                logger.info(f"[INFO] Oferta propria carga_id: {current_oferta_data.get('carga_id', 'N/A')}")

            motorista_data = {
                'nome': motorista.get('nome', 'Motorista')
            }

            try:
                parametros = build_template_parameters(motorista_data, current_oferta_data)
                lista_parametros.append(parametros)

                template_message = build_template_message(parametros)
                template_messages[telefone] = template_message

                logger.info(f"[INFO] Template para {telefone}: {len(template_message)} caracteres")

            except Exception as e:
                logger.error(f"[ERRO] Falha ao montar template para {telefone}: {str(e)}")
                return {
                    'statusCode': 400,
                    'body': json.dumps({
                        'success': False,
                        'message': f'Failed to build template for {telefone}: {str(e)}'
                    })
                }

    if not lista_telefones:
        logger.error("[ERRO] Nenhum telefone valido encontrado")
        return {
            'statusCode': 400,
            'body': json.dumps({
                'success': False,
                'message': 'No valid phone numbers found in motoristas list'
            })
        }

    result = {
        'motoristas_count': len(motoristas),
        'template_used': template_nome,
        'lista_telefones': lista_telefones,
        'lista_parametros': lista_parametros,
        'multi_offer': multi_offer,
        'context_type': 'FLUXO_ATIVO_MULTI_OFFER' if multi_offer else 'FLUXO_ATIVO_WITH_COMPLETE_SESSION_ATTRIBUTES'
    }

    try:
        logger.info(f"[INFO] Etapa 1: enviando template em lote para {len(lista_telefones)} motoristas")
        batch_template_result = send_template_message_batch(
            config['api_url'],
            config['jwt_token'],
            lista_telefones,
            template_nome,
            lista_parametros
        )
        result['batch_template'] = batch_template_result

        if not batch_template_result['success']:
            result['success'] = False
            result['error'] = 'Failed to send batch template'
            logger.error("[ERRO] Falha no envio do template em lote")

            return {
                'statusCode': 500,
                'body': json.dumps(result, ensure_ascii=False)
            }

        logger.info("[INFO] Etapa 2: processando passos pos-template com session attributes completos")

        post_steps_results = process_post_template_steps_batch(
            config['api_url'],
            config['jwt_token'],
            lista_telefones,
            chatbot_lambda_name,
            template_messages,
            motoristas_info,
            session_attributes_por_telefone
        )
        result['driver_results'] = post_steps_results

        successful_drivers = sum(1 for r in post_steps_results.values() if r.get('success'))
        result['successful_drivers'] = successful_drivers
        result['failed_drivers'] = len(lista_telefones) - successful_drivers

        result['success'] = True

        logger.info(f"[INFO] Envio concluido: {successful_drivers}/{len(lista_telefones)} motoristas com sucesso")
        if multi_offer:
            logger.info("[INFO] Modo multi-offer: cada motorista recebeu oferta propria")
        logger.info("[INFO] Chatbot recebeu session attributes completos")

        return {
            'statusCode': 200,
            'body': json.dumps(result, ensure_ascii=False)
        }

    except Exception as e:
        logger.error(f"[ERRO] Falha critica no processamento: {str(e)}")
        import traceback
        traceback.print_exc()

        result['success'] = False
        result['error'] = str(e)

        return {
            'statusCode': 500,
            'body': json.dumps(result, ensure_ascii=False)
        }