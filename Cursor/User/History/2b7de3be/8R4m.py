"""
Action group para consulta de ofertas de carga disponiveis na API Rodosafra
Busca ofertas com filtros opcionais de periodo, origem, destino e tipo de veiculo
"""
import json
import os
import logging
import requests
import boto3
from typing import Dict, Any, Tuple, Optional, List
from botocore.exceptions import ClientError
from datetime import datetime, timedelta, timezone
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


def _gerar_periodo_padrao() -> Tuple[str, str]:
    """
    Gera periodo padrao para busca (hoje ate dois dias a frente)

    Input: None
    Output: (tuple) - (inicio_periodo, fim_periodo) no formato YYYY-MM-DD
    """
    hoje = datetime.now(timezone.utc)
    depois_amanha = hoje + timedelta(days=2)

    inicio = hoje.strftime("%Y-%m-%d")
    fim = depois_amanha.strftime("%Y-%m-%d")

    logger.info(f"[CONSULTA] Periodo padrao gerado - inicio: {inicio}, fim: {fim}")

    return inicio, fim


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


def _extrair_veiculos_de_arrays(params: Dict) -> Optional[Dict[str, Any]]:
    """
    Extrai IDs de tipos de veiculo e equipamentos dos parametros

    Input: params (dict) - Parametros com tipos_veiculo_ids
    Output: (dict) - VeiculoDTO ou None
    """
    veiculo_ids = params.get('tipos_veiculo_ids', [])

    if isinstance(veiculo_ids, str):
        try:
            veiculo_ids = json.loads(veiculo_ids)
            logger.info(f"[VALIDACAO] Array JSON parseado: {veiculo_ids}")
        except (json.JSONDecodeError, ValueError):
            veiculo_ids = veiculo_ids.strip('[]').strip()
            veiculo_ids = [int(x.strip()) for x in veiculo_ids.split(',') if x.strip()]
            logger.info(f"[VALIDACAO] Array parseado como CSV: {veiculo_ids}")

    if isinstance(veiculo_ids, list):
        veiculo_ids = [int(x) if not isinstance(x, int) else x for x in veiculo_ids]

    if not veiculo_ids:
        return None

    veiculo_dto = {
        'tipoIds': veiculo_ids,
        'equipamentoIds': veiculo_ids
    }

    logger.info(f"[CONSULTA] Veiculo IDs extraidos: {veiculo_ids}")

    return veiculo_dto


def _extrair_local_de_params(params: Dict, prefixo: str) -> Optional[Dict[str, Any]]:
    """
    Extrai endereco (cidade) dos parametros para origem ou destino

    Input: params (dict) - Parametros do action group
           prefixo (str) - 'origem' ou 'destino'
    Output: (dict) - Endereco ou None
    """
    cidade = params.get(f'{prefixo}_cidade')

    if not cidade:
        return None

    endereco = {'cidade': cidade}

    logger.info(f"[CONSULTA] {prefixo.capitalize()} extraida: {cidade}")

    return endereco


def _extrair_veiculos_session(session: Dict) -> Dict[str, Any]:
    """
    Extrai tipos de veiculo e equipamentos dos atributos da sessao

    Input: session (dict) - Atributos da sessao com veiculos
    Output: (dict) - VeiculoDTO ou None
    """
    veiculos_json = session.get('veiculos', '[]')

    try:
        veiculos = json.loads(veiculos_json) if isinstance(veiculos_json, str) else veiculos_json
    except Exception as e:
        logger.warning(f"[VALIDACAO] Erro ao parsear veiculos: {e}")
        veiculos = []

    if not veiculos:
        return None

    tipos_veiculo = set()
    equipamentos = set()

    for veiculo in veiculos:
        tipo_nome = veiculo.get('tipo_veiculo_nome', '')
        equip_nome = veiculo.get('tipo_equipamento_nome', '')

        if tipo_nome:
            tipos_veiculo.add(tipo_nome)
        if equip_nome:
            equipamentos.add(equip_nome)

    veiculo_dto = {}

    if tipos_veiculo:
        veiculo_dto['tipos'] = list(tipos_veiculo)

    if equipamentos:
        veiculo_dto['equipamentos'] = list(equipamentos)

    return veiculo_dto if veiculo_dto else None


def consultar_ofertas(params: Dict, session: Dict) -> Dict[str, Any]:
    """
    Busca ofertas de carga disponiveis na API Rodosafra com filtros opcionais

    Input: params (dict) - Parametros opcionais de busca
           session (dict) - Atributos da sessao com dados do motorista
    Output: (dict) - Lista de ofertas encontradas
    """
    logger.info("[CONSULTA] Iniciando consulta de ofertas")

    autenticado, auth_ou_erro = autenticar_api()
    if not autenticado:
        logger.error(f"[AUTH] Falha na autenticacao: {auth_ou_erro}")
        return {
            "status": "erro",
            "mensagem": f"Erro de autenticacao: {auth_ou_erro}"
        }

    inicio_periodo = params.get('inicio_periodo')
    fim_periodo = params.get('fim_periodo')

    if not inicio_periodo or not fim_periodo:
        if not inicio_periodo:
            inicio_periodo = (
                session.get('inicio_periodo') or
                session.get('embarque_previsao_carregamento')
            )
        if not fim_periodo:
            fim_periodo = session.get('fim_periodo')

    if not inicio_periodo or not fim_periodo:
        inicio_periodo, fim_periodo = _gerar_periodo_padrao()
        logger.info("[CONSULTA] Usando periodo padrao")

    payload = {
        "inicio_periodo": inicio_periodo,
        "fim_periodo": fim_periodo
    }

    logger.info(f"[CONSULTA] Periodo configurado - inicio: {inicio_periodo}, fim: {fim_periodo}")

    telefone = (
        session.get('telefone') or
        session.get('motorista_telefone')
    )

    if telefone:
        telefone_limpo = ''.join(filter(str.isdigit, telefone))
        if telefone_limpo.startswith('55'):
            telefone_limpo = telefone_limpo[2:]

        if len(telefone_limpo) in [10, 11]:
            payload['telefone_motorista'] = telefone_limpo
            logger.info(f"[CONSULTA] Telefone configurado")
        else:
            logger.warning(f"[VALIDACAO] Telefone invalido, sera ignorado")

    origem = _extrair_local_de_params(params, 'origem')
    if not origem:
        origem_cidade = session.get('carga_local_coleta_cidade')
        if origem_cidade:
            if ' - ' in origem_cidade:
                cidade, uf = origem_cidade.split(' - ', 1)
                origem = {'cidade': cidade.strip(), 'uf': uf.strip()}
            else:
                origem = {'cidade': origem_cidade}

    if origem:
        payload['origem'] = origem
        logger.info(f"[CONSULTA] Origem configurada: {origem}")

    destino = _extrair_local_de_params(params, 'destino')
    if not destino:
        destino_cidade = session.get('carga_local_entrega_cidade')
        if destino_cidade:
            if ' - ' in destino_cidade:
                cidade, uf = destino_cidade.split(' - ', 1)
                destino = {'cidade': cidade.strip(), 'uf': uf.strip()}
            else:
                destino = {'cidade': destino_cidade}

    if destino:
        payload['destino'] = destino
        logger.info(f"[CONSULTA] Destino configurado: {destino}")

    veiculo_dto = _extrair_veiculos_de_arrays(params)

    if not veiculo_dto:
        tipos_veiculo = []
        equipamentos = []

        tipo_principal = session.get('veiculo_principal_tipo_id')
        if tipo_principal:
            tipos_veiculo.append(int(tipo_principal))

        for i in range(1, 4):
            tipo_equip = session.get(f'equipamento_{i}_tipo_equipamento_id')
            if tipo_equip:
                equipamentos.append(int(tipo_equip))

        if tipos_veiculo or equipamentos:
            veiculo_dto = {}
            if tipos_veiculo:
                veiculo_dto['tipoIds'] = tipos_veiculo
            if equipamentos:
                veiculo_dto['equipamentoIds'] = equipamentos

    if veiculo_dto:
        payload['veiculo'] = veiculo_dto
        logger.info(f"[CONSULTA] Veiculo configurado")

    logger.info(f"[CONSULTA] Payload preparado com {len(payload)} campos")

    telefone_session = session.get('telefone') or session.get('conversa_id')

    try:
        url = f"{API_BASE_URL}/publico/carga/ofertas"

        logger.info(f"[API] Chamando {url}")
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
            operation_name="Consultar ofertas",
            telefone=telefone_session
        )

        logger.info(f"[API] Resposta recebida - Status: {response.status_code}")

        if response.status_code == 200:
            ofertas = response.json()

            if not ofertas or len(ofertas) == 0:
                logger.info("[CONSULTA] Nenhuma oferta disponivel")
                return {
                    "status": "sucesso",
                    "quantidade": 0,
                    "mensagem": "Nenhuma oferta disponivel no momento",
                    "ofertas": []
                }

            ofertas_formatadas = []

            for oferta in ofertas[:10]:
                try:
                    oferta_formatada = {
                        "origem": {
                            "cidade": oferta.get('origem', {}).get('endereco', {}).get('cidade', 'N/A'),
                            "uf": oferta.get('origem', {}).get('endereco', {}).get('uf', 'N/A'),
                            "nome_local": oferta.get('origem', {}).get('nomeLocal', '')
                        },
                        "destino": {
                            "cidade": oferta.get('destino', {}).get('endereco', {}).get('cidade', 'N/A'),
                            "uf": oferta.get('destino', {}).get('endereco', {}).get('uf', 'N/A'),
                            "nome_local": oferta.get('destino', {}).get('nomeLocal', '')
                        },
                        "carga": {
                            "produto": oferta.get('carga', {}).get('produto', 'N/A'),
                            "id": oferta.get('carga', {}).get('id'),
                            "saldo": oferta.get('carga', {}).get('saldo'),
                            "programacoes": oferta.get('carga', {}).get('programacoes', [])
                        },
                        "preco": {
                            "valor": oferta.get('preco', {}).get('valor'),
                            "pedagio_incluso": oferta.get('preco', {}).get('pedagio_incluso', False),
                            "tipo_pagamento": oferta.get('preco', {}).get('tipo_pagamento', 'N/A')
                        },
                        "veiculo_requerido": {
                            "tipo_ids": oferta.get('veiculo', {}).get('tipoIds', []),
                            "equipamento_ids": oferta.get('veiculo', {}).get('equipamentoIds', [])
                        }
                    }
                    ofertas_formatadas.append(oferta_formatada)
                except Exception as e:
                    logger.warning(f"[CONSULTA] Erro ao formatar oferta: {e}")
                    continue

            logger.info(f"[CONSULTA] {len(ofertas_formatadas)} ofertas formatadas")

            return {
                "status": "sucesso",
                "quantidade": len(ofertas_formatadas),
                "mensagem": f"Encontradas {len(ofertas_formatadas)} ofertas disponiveis entre {inicio_periodo} e {fim_periodo}",
                "ofertas": ofertas_formatadas
            }

        elif response.status_code == 204:
            logger.info("[CONSULTA] Nenhuma oferta disponivel (204)")
            return {
                "status": "sucesso",
                "quantidade": 0,
                "mensagem": "Nenhuma oferta disponivel no momento",
                "ofertas": []
            }

        elif response.status_code == 400:
            erro_msg = response.json().get('mensagem', 'Erro de validacao')
            logger.error(f"[API] Erro 400 - Validacao: {erro_msg}")
            return {
                "status": "erro",
                "mensagem": f"Dados invalidos: {erro_msg}"
            }

        elif response.status_code == 403:
            logger.error("[API] Motorista bloqueado ou sem permissao")
            return {
                "status": "erro",
                "mensagem": "Motorista bloqueado ou sem permissao"
            }

        else:
            if response.status_code >= 500:
                log_api_error(
                    api_route="/publico/carga/ofertas",
                    error_code=response.status_code,
                    error_message=f"Erro ao consultar ofertas (HTTP {response.status_code})",
                    payload=payload,
                    response_body=response.text
                )

            logger.error(f"[API] Erro HTTP inesperado: {response.status_code}")
            return {
                "status": "erro",
                "mensagem": f"Erro ao consultar ofertas (HTTP {response.status_code})",
                "detalhe": response.text[:200] if response.text else ""
            }

    except requests.exceptions.Timeout:
        logger.error("[API] Timeout na requisicao")
        return {
            "status": "erro",
            "mensagem": "Timeout ao consultar ofertas - tente novamente"
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
    Handler principal do Lambda para action group de consulta de ofertas

    Input: event (dict) - Evento do Bedrock Agent com parametros e sessao
           context (Any) - Contexto do Lambda
    Output: (dict) - Resposta formatada para Bedrock Agent
    """
    logger.info(f"[HANDLER] Event: {json.dumps(event, ensure_ascii=False)}")
    logger.info("[HANDLER] Iniciando action group - Consultar Ofertas")

    action_group = event.get('actionGroup', 'ConsultarOfertas')
    function_name = event.get('function', 'consultar_ofertas')

    try:
        parameters = {p.get('name'): p.get('value') for p in event.get('parameters', [])}
        session_attributes = event.get('sessionAttributes', {})

        logger.info(f"[HANDLER] Funcao: {function_name}")
        logger.info(f"[HANDLER] Atributos de sessao disponiveis: {list(session_attributes.keys())}")

        if function_name == 'consultar_ofertas':
            resultado = consultar_ofertas(parameters, session_attributes)
        else:
            logger.warning(f"[HANDLER] Funcao desconhecida: {function_name}")
            resultado = {
                "status": "erro",
                "mensagem": f"Funcao desconhecida: {function_name}. Use consultar_ofertas"
            }

        logger.info(f"[HANDLER] Processamento concluido - Status: {resultado.get('status')}")

    except Exception as e:
        logger.error(f"[ERRO] Excecao critica no handler: {str(e)}", exc_info=True)

        resultado = {
            "status": "erro",
            "mensagem": "Ocorreu um erro ao consultar as ofertas. Por favor, tente novamente.",
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
