"""
Action group para cadastro de motorista na API Rodosafra
Registra novo motorista apos verificacao de cadastro inexistente
"""
import json
import os
import re
import logging
import requests
import boto3
from typing import Dict, Any, Tuple, Optional
from botocore.exceptions import ClientError
from api_error_logger import log_api_error
from api_retry_util import retry_on_timeout

logger = logging.getLogger()
logger.setLevel(logging.INFO)

API_BASE_URL = os.environ.get('RODOSAFRA_API_BASE_URL', 'https://api-staging.rodosafra.net/api')

ssm_client = boto3.client('ssm')

PARAMETER_STORE_TOKEN_NAME = os.environ.get(
    'PARAMETER_STORE_TOKEN_NAME',
    '/rodosafra/auth/token'
)

auth_cookie = None


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


def _limpar_numeros(valor: str) -> str:
    """
    Remove caracteres nao numericos e codigo de pais 55 se presente

    Input: valor (str) - String com numeros e possivelmente formatacao
    Output: (str) - Apenas digitos sem codigo de pais
    """
    telefone = re.sub(r'\D', '', valor)
    if telefone.startswith('55'):
        telefone = telefone[2:]
    return telefone


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


def cadastrar_motorista(params: Dict, session: Dict) -> Dict[str, Any]:
    """
    Cadastra motorista na API Rodosafra em dois passos

    Input: params (dict) - Parametros da funcao com dados do motorista
           session (dict) - Atributos da sessao com dados adicionais
    Output: (dict) - Status do cadastro e ID do motorista
    """
    logger.info("[CADASTRO] Iniciando processo de cadastro de motorista")

    telefone_raw = _obter_valor_com_prioridade(
        params,
        session,
        ['telefone', 'fone', 'motorista_telefone']
    )

    nome = _obter_valor_com_prioridade(
        params,
        session,
        ['nome', 'nome_completo', 'nome_motorista', 'motorista_nome', 'nome_completo_motorista']
    )

    cpf_raw = _obter_valor_com_prioridade(
        params,
        session,
        ['cpf', 'motorista_cpf', 'cpf_motorista']
    )

    telefone = _limpar_numeros(telefone_raw)
    cpf = _limpar_numeros(cpf_raw)

    logger.info(f"[CADASTRO] Dados extraidos - Nome: {nome}, Telefone: {len(telefone)} digitos, CPF: {len(cpf)} digitos")

    erros = []

    if not nome or len(nome.strip()) < 3:
        erros.append("Nome invalido ou ausente")

    if len(telefone) != 11:
        erros.append(f"Telefone deve ter 11 digitos (recebido: {len(telefone)})")

    if len(cpf) != 11:
        erros.append(f"CPF deve ter 11 digitos (recebido: {len(cpf)})")

    if erros:
        logger.error(f"[VALIDACAO] Erros encontrados: {erros}")
        return {
            "status": "erro",
            "mensagem": "Dados invalidos ou incompletos",
            "erros": erros,
            "detalhes": {
                "telefone_recebido": telefone_raw,
                "cpf_recebido": cpf_raw,
                "nome_recebido": nome
            }
        }

    autenticado, auth_ou_erro = autenticar_api()
    if not autenticado:
        logger.error(f"[AUTH] Falha na autenticacao: {auth_ou_erro}")
        return {
            "status": "erro",
            "mensagem": f"Erro de autenticacao: {auth_ou_erro}"
        }

    payload = {
        "telefone": telefone,
        "nome": nome.strip(),
        "cpf": cpf
    }

    logger.info(f"[CADASTRO] Payload preparado")

    telefone_session = session.get('telefone') or session.get('conversa_id')

    try:
        url = f"{API_BASE_URL}/publico/motorista/v1/cadastro"

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
                timeout=15
            ),
            max_retries=3,
            operation_name="Cadastrar motorista (step 1)",
            telefone=telefone_session
        )

        logger.info(f"[API] Resposta recebida - Status: {response.status_code}")

        if response.status_code == 201:
            motorista_id = response.json()

            logger.info(f"[CADASTRO] Motorista cadastrado com sucesso - ID: {motorista_id}")

            try:
                url_automatico = f"{API_BASE_URL}/publico/motorista/v1/cadastro-automatico"

                logger.info(f"[API] Iniciando cadastro automatico completo - Step 2")
                logger.info(f"[API] Payload para {url_automatico}: {json.dumps(payload, ensure_ascii=False)}")

                response_automatico = requests.post(
                    url_automatico,
                    json=payload,
                    headers={
                        'Cookie': auth_cookie,
                        'Content-Type': 'application/json'
                    },
                    timeout=15
                )

                logger.info(f"[API] Cadastro automatico - Status: {response_automatico.status_code}")

                if response_automatico.status_code >= 500:
                    log_api_error(
                        api_route="/publico/motorista/v1/cadastro-automatico",
                        error_code=response_automatico.status_code,
                        error_message=f"Erro no cadastro automatico completo (HTTP {response_automatico.status_code})",
                        payload=payload,
                        response_body=response_automatico.text
                    )
                    logger.warning(f"[API] Cadastro automatico falhou, mas step 1 teve sucesso")

            except Exception as e:
                logger.warning(f"[API] Erro no cadastro automatico (step 2), mas continuando: {str(e)}")

            return {
                "status": "sucesso",
                "mensagem": "Motorista cadastrado com sucesso na Rodosafra",
                "motorista_id": motorista_id,
                "dados_cadastrados": {
                    "nome": nome.strip(),
                    "telefone": telefone,
                    "cpf": cpf
                }
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
            logger.warning("[API] Motorista ja existe com este CPF")

            return {
                "status": "ja_existe",
                "mensagem": "Ja existe um motorista cadastrado com este CPF",
                "sugestao": "Verifique se o cadastro ja foi feito anteriormente"
            }

        elif response.status_code == 500:
            logger.error("[API] Erro interno no servidor")

            log_api_error(
                api_route="/publico/motorista/v1/cadastro",
                error_code=500,
                error_message="Erro interno no servidor ao cadastrar motorista",
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
                    api_route="/publico/motorista/v1/cadastro",
                    error_code=response.status_code,
                    error_message=f"Erro inesperado ao cadastrar motorista (HTTP {response.status_code})",
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
            "mensagem": "Timeout ao cadastrar motorista - tente novamente"
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
    Handler principal do Lambda para action group de cadastro de motorista

    Input: event (dict) - Evento do Bedrock Agent com parametros e sessao
           context (Any) - Contexto do Lambda
    Output: (dict) - Resposta formatada para Bedrock Agent
    """
    logger.info(f"[HANDLER] Event: {json.dumps(event, ensure_ascii=False)}")
    logger.info("[HANDLER] Iniciando action group - Cadastrar Motorista")

    action_group = event.get('actionGroup', 'CadastrarMotorista')
    function_name = event.get('function', 'cadastrar_motorista')

    try:
        parameters = {p.get('name'): p.get('value') for p in event.get('parameters', [])}
        session_attributes = event.get('sessionAttributes', {})

        logger.info(f"[HANDLER] Funcao: {function_name}")
        logger.info(f"[HANDLER] Atributos de sessao disponiveis: {list(session_attributes.keys())}")

        if function_name == 'cadastrar_motorista':
            resultado = cadastrar_motorista(parameters, session_attributes)
        else:
            logger.warning(f"[HANDLER] Funcao desconhecida: {function_name}")
            resultado = {
                "status": "erro",
                "mensagem": f"Funcao desconhecida: {function_name}. Use cadastrar_motorista"
            }

        logger.info(f"[HANDLER] Processamento concluido - Status: {resultado.get('status')}")

    except Exception as e:
        logger.error(f"[ERRO] Excecao critica no handler: {str(e)}", exc_info=True)

        resultado = {
            "status": "erro",
            "mensagem": "Ocorreu um erro ao processar o cadastro. Por favor, tente novamente.",
            "detalhe_tecnico": str(e)[:200]
        }

    return {
        'messageVersion': '1.0',
        'response': {
            'actionGroup': action_group,
            'function': function_name,
            'functionResponse': {
                'responseBody': {
                    'TEXT': {
                        'body': json.dumps(resultado, ensure_ascii=False)
                    }
                }
            }
        }
    }
