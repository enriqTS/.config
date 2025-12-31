"""
Action group para criacao de embarque na API Rodosafra
Cria embarque validando tipo de veiculo e periodo de disponibilidade da carga

Funcionalidades:
- Valida tipo de veiculo permitido para a carga
- Valida periodo de disponibilidade da carga
- Retorna payload completo da API no response para o chatbot
- Permite que o chatbot veja a data real cadastrada (evita discrepancias)
"""
import json
import os
import re
import logging
import requests
import boto3
from typing import Dict, Any, Tuple, Optional
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key
from datetime import datetime, timezone, timedelta
from api_error_logger import log_api_error
from api_retry_util import retry_on_timeout
from transbordo_caller import executar_transbordo

logger = logging.getLogger()
logger.setLevel(logging.INFO)

API_BASE_URL = os.environ.get('RODOSAFRA_API_BASE_URL', 'https://api-staging.rodosafra.net/api')

ssm_client = boto3.client('ssm')

dynamodb = boto3.resource('dynamodb')

PARAMETER_STORE_TOKEN_NAME = os.environ.get(
    'PARAMETER_STORE_TOKEN_NAME',
    '/rodosafra/auth/token'
)

MOTORISTAS_TABLE = os.environ.get('MOTORISTAS_TABLE', 'motoristas')
OFERTAS_TABLE = os.environ.get('OFERTAS_TABLE', 'ofertas')
NEGOCIACAO_TABLE = os.environ.get('NEGOCIACAO_TABLE', 'negociacao')
EQUIPAMENTOS_TABLE = os.environ.get('EQUIPAMENTOS_TABLE', 'equipamentos')
VEICULOS_TABLE = os.environ.get('VEICULOS_TABLE', 'veiculos')

motoristas_table = dynamodb.Table(MOTORISTAS_TABLE)
ofertas_table = dynamodb.Table(OFERTAS_TABLE)
veiculos_table = dynamodb.Table(VEICULOS_TABLE)

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


def _buscar_veiculo_e_equipamentos_por_telefone(telefone: str) -> Tuple[Optional[int], list, Optional[str]]:
    """
    Busca veiculo_cavalo_id e equipamento_ids da tabela negociacao

    Input: telefone (str) - Telefone do motorista
    Output: (tuple) - (veiculo_cavalo_id, equipamento_ids, mensagem_erro)
    """
    if not telefone:
        return None, [], "Telefone nao fornecido"

    try:
        telefone_limpo = telefone.strip()
        telefone_limpo = re.sub(r'\D', '', telefone_limpo)

        if not telefone_limpo.startswith('55'):
            telefone_limpo = '55' + telefone_limpo

        logger.info(f"[NEGOCIACAO] Buscando veiculo_cavalo_id e equipamento_ids para telefone: {telefone_limpo}")

        negociacao_table = dynamodb.Table(NEGOCIACAO_TABLE)

        response = negociacao_table.query(
            KeyConditionExpression=Key('telefone').eq(telefone_limpo),
            ScanIndexForward=False,
            Limit=1,
            ProjectionExpression='veiculo_cavalo_id, equipamento_ids'
        )

        items = response.get('Items', [])

        if not items:
            logger.warning(f"[NEGOCIACAO] Nenhum registro encontrado para telefone: {telefone_limpo}")
            return None, [], "Nenhum registro encontrado na tabela negociacao"

        item = items[0]

        veiculo_cavalo_id = item.get('veiculo_cavalo_id')
        veiculo_cavalo_id_int = None

        if veiculo_cavalo_id:
            try:
                veiculo_cavalo_id_int = int(veiculo_cavalo_id)
                logger.info(f"[NEGOCIACAO] veiculo_cavalo_id encontrado: {veiculo_cavalo_id_int}")
            except (ValueError, TypeError):
                logger.warning(f"[NEGOCIACAO] veiculo_cavalo_id invalido: {veiculo_cavalo_id}")

        equipamento_ids_raw = item.get('equipamento_ids', [])
        equipamento_ids = []

        if equipamento_ids_raw:
            logger.info(f"[NEGOCIACAO] equipamento_ids raw: {equipamento_ids_raw}")

            for eq_item in equipamento_ids_raw:
                if isinstance(eq_item, dict):
                    eq_id_str = eq_item.get('N')
                    if eq_id_str:
                        try:
                            equipamento_ids.append(int(eq_id_str))
                        except (ValueError, TypeError):
                            logger.warning(f"[NEGOCIACAO] equipamento_id invalido: {eq_id_str}")
                else:
                    try:
                        equipamento_ids.append(int(eq_item))
                    except (ValueError, TypeError):
                        logger.warning(f"[NEGOCIACAO] equipamento_id invalido: {eq_item}")

            if equipamento_ids:
                logger.info(f"[NEGOCIACAO] {len(equipamento_ids)} equipamento_ids encontrados: {equipamento_ids}")

        return veiculo_cavalo_id_int, equipamento_ids, None

    except ClientError as e:
        error_code = e.response['Error']['Code']
        logger.error(f"[NEGOCIACAO] Erro DynamoDB: {error_code}")
        return None, [], f"Erro ao buscar dados na tabela negociacao: {error_code}"

    except Exception as e:
        logger.error(f"[NEGOCIACAO] Erro: {str(e)}", exc_info=True)
        return None, [], f"Erro ao buscar dados: {str(e)}"


def _buscar_carga_id_por_telefone(telefone: str) -> Tuple[Optional[int], Optional[str]]:
    """
    Busca carga_id usando telefone do motorista via tabelas motoristas e ofertas

    Input: telefone (str) - Telefone do motorista
    Output: (tuple) - (carga_id, mensagem_erro)
    """
    if not telefone:
        return None, "Telefone nao fornecido"

    try:
        telefone_limpo = telefone.strip()

        telefone_limpo = re.sub(r'\D', '', telefone_limpo)

        if not telefone_limpo.startswith('55'):
            telefone_limpo = '55' + telefone_limpo

        logger.info(f"[CARGA] Buscando oferta_id para telefone: {telefone_limpo}")

        response = motoristas_table.query(
            IndexName='telefone-index',
            KeyConditionExpression=Key('telefone').eq(telefone_limpo)
        )

        if 'Items' not in response or len(response['Items']) == 0:
            logger.warning(f"[CARGA] Motorista nao encontrado com telefone: {telefone_limpo}")
            return None, "Motorista nao encontrado no sistema"

        motorista = response['Items'][0]
        oferta_id = motorista.get('oferta_id')

        if not oferta_id:
            logger.warning(f"[CARGA] Motorista {telefone_limpo} nao possui oferta_id")
            return None, "Nenhuma oferta associada ao motorista"

        logger.info(f"[CARGA] Oferta ID encontrado: {oferta_id}")

        response = ofertas_table.get_item(
            Key={'id_oferta': str(oferta_id)}
        )

        if 'Item' not in response:
            logger.warning(f"[CARGA] Oferta nao encontrada com id: {oferta_id}")
            return None, f"Oferta {oferta_id} nao encontrada no sistema"

        oferta = response['Item']
        carga_id = oferta.get('carga_id')

        if not carga_id:
            logger.warning(f"[CARGA] Oferta {oferta_id} nao possui carga_id")
            return None, "Oferta nao possui carga associada"

        logger.info(f"[CARGA] Carga ID encontrado automaticamente: {carga_id}")
        return int(carga_id), None

    except ClientError as e:
        error_code = e.response['Error']['Code']

        if error_code == 'ResourceNotFoundException':
            logger.error("[CARGA] Indice telefone-index nao encontrado na tabela motoristas")
            return None, "Erro de configuracao: indice de telefone nao encontrado na tabela"

        elif error_code == 'ValidationException':
            logger.error(f"[CARGA] Erro de validacao ao consultar tabela motoristas: {str(e)}")
            return None, f"Erro ao buscar motorista: {str(e)}"

        else:
            logger.error(f"[CARGA] Erro DynamoDB ao buscar motorista: {error_code}")
            return None, f"Erro ao buscar motorista no sistema: {error_code}"

    except Exception as e:
        logger.error(f"[CARGA] Erro ao buscar carga_id: {str(e)}", exc_info=True)
        return None, f"Erro ao buscar oferta: {str(e)}"


def _obter_equipamentos_por_veiculo_id(veiculo_id: int, telefone: str = None) -> list:
    """
    Busca IDs de equipamentos associados a um veiculo

    Input: veiculo_id (int) - ID do veiculo cavalo/caminhao
           telefone (str) - Telefone do motorista para busca na negociacao como fallback
    Output: (list) - Lista de IDs de equipamentos ou lista vazia
    """
    if not veiculo_id:
        logger.warning("[EQUIPAMENTOS] veiculo_id nao fornecido")
        return []

    equipamentos_encontrados = []

    try:
        equipamentos_table = dynamodb.Table(EQUIPAMENTOS_TABLE)

        logger.info(f"[EQUIPAMENTOS] Buscando equipamentos para veiculo_id: {veiculo_id}")

        response = equipamentos_table.query(
            IndexName='id_veiculo-index',
            KeyConditionExpression=Key('id_veiculo').eq(str(veiculo_id))
        )

        if 'Items' in response and len(response['Items']) > 0:
            logger.info(f"[EQUIPAMENTOS] Encontrados {len(response['Items'])} equipamentos na tabela equipamentos")

            for item in response['Items']:
                equipamento_id = item.get('id_equipamento')
                placa = item.get('placa', 'N/A')
                tipo = item.get('tipo_equipamento_nome', 'N/A')

                if equipamento_id:
                    try:
                        eq_id_int = int(equipamento_id)
                        equipamentos_encontrados.append(eq_id_int)
                        logger.info(f"[EQUIPAMENTOS] Equipamento ID: {eq_id_int}, Placa: {placa}, Tipo: {tipo}")
                    except (ValueError, TypeError):
                        logger.warning(f"[EQUIPAMENTOS] ID invalido: {equipamento_id}")

            if equipamentos_encontrados:
                logger.info(f"[EQUIPAMENTOS] Total de equipamentos validos: {len(equipamentos_encontrados)}")
                return equipamentos_encontrados
        else:
            logger.info(f"[EQUIPAMENTOS] Nenhum equipamento encontrado na tabela equipamentos para veiculo_id: {veiculo_id}")

    except ClientError as e:
        error_code = e.response['Error']['Code']

        if error_code == 'ResourceNotFoundException':
            logger.warning("[EQUIPAMENTOS] GSI id_veiculo-index nao encontrado na tabela equipamentos")
        else:
            logger.error(f"[EQUIPAMENTOS] Erro ao buscar na tabela equipamentos: {error_code}")

    except Exception as e:
        logger.error(f"[EQUIPAMENTOS] Erro ao buscar equipamentos: {str(e)}", exc_info=True)

    if not equipamentos_encontrados and telefone:
        try:
            negociacao_table = dynamodb.Table(NEGOCIACAO_TABLE)

            logger.info(f"[EQUIPAMENTOS] Fallback - Buscando equipamento_ids na negociacao para telefone: {telefone}")

            response = negociacao_table.query(
                KeyConditionExpression=Key('telefone').eq(telefone),
                ScanIndexForward=False,
                Limit=1,
                ProjectionExpression='equipamento_ids'
            )

            items = response.get('Items', [])

            if items:
                item = items[0]
                equipamento_ids = item.get('equipamento_ids', [])

                if equipamento_ids:
                    logger.info(f"[EQUIPAMENTOS] Encontrados {len(equipamento_ids)} IDs de equipamentos na negociacao")

                    for eq_id in equipamento_ids:
                        try:
                            equipamentos_encontrados.append(int(eq_id))
                        except (ValueError, TypeError):
                            logger.warning(f"[EQUIPAMENTOS] ID de equipamento invalido na negociacao: {eq_id}")

                    if equipamentos_encontrados:
                        logger.info(f"[EQUIPAMENTOS] Fallback bem-sucedido: {len(equipamentos_encontrados)} equipamentos")
                        return equipamentos_encontrados

        except Exception as e:
            logger.error(f"[EQUIPAMENTOS] Erro no fallback negociacao: {str(e)}")

    if not equipamentos_encontrados:
        logger.info(f"[EQUIPAMENTOS] Nenhum equipamento encontrado para veiculo_id {veiculo_id}")

    return equipamentos_encontrados


def _obter_equipamentos_por_placas(placas: list) -> list:
    """
    Busca IDs de equipamentos pela placa usando GSI na tabela equipamentos

    Input: placas (list) - Lista de placas de equipamentos
    Output: (list) - Lista de IDs de equipamentos ou lista vazia
    """
    if not placas:
        return []

    equipamentos_encontrados = []

    try:
        equipamentos_table = dynamodb.Table(EQUIPAMENTOS_TABLE)

        logger.info(f"[EQUIPAMENTOS-PLACAS] Buscando equipamentos por placas: {placas}")

        for placa in placas:
            if not placa or not isinstance(placa, str):
                continue

            placa_limpa = re.sub(r'[^A-Z0-9]', '', placa.upper())

            if len(placa_limpa) != 7:
                logger.warning(f"[EQUIPAMENTOS-PLACAS] Placa invalida: {placa}")
                continue

            try:
                response = equipamentos_table.query(
                    IndexName='placa-index',
                    KeyConditionExpression=Key('placa').eq(placa_limpa)
                )

                if 'Items' in response and len(response['Items']) > 0:
                    item = response['Items'][0]
                    equipamento_id = item.get('id_equipamento')

                    if equipamento_id:
                        try:
                            eq_id_int = int(equipamento_id)
                            equipamentos_encontrados.append(eq_id_int)
                            logger.info(f"[EQUIPAMENTOS-PLACAS] Placa {placa_limpa} -> ID: {eq_id_int}")
                        except (ValueError, TypeError):
                            logger.warning(f"[EQUIPAMENTOS-PLACAS] ID invalido para placa {placa_limpa}: {equipamento_id}")
                else:
                    logger.info(f"[EQUIPAMENTOS-PLACAS] Placa {placa_limpa} nao encontrada")

            except ClientError as e:
                error_code = e.response['Error']['Code']

                if error_code == 'ResourceNotFoundException':
                    logger.warning("[EQUIPAMENTOS-PLACAS] GSI placa-index nao encontrado na tabela equipamentos")
                    break
                else:
                    logger.error(f"[EQUIPAMENTOS-PLACAS] Erro ao buscar placa {placa_limpa}: {error_code}")

    except Exception as e:
        logger.error(f"[EQUIPAMENTOS-PLACAS] Erro ao buscar equipamentos por placas: {str(e)}", exc_info=True)

    if equipamentos_encontrados:
        logger.info(f"[EQUIPAMENTOS-PLACAS] Total encontrado: {len(equipamentos_encontrados)} equipamentos")

    return equipamentos_encontrados


def _eh_placa_brasileira(texto: str) -> bool:
    """
    Verifica se texto parece ser uma placa brasileira

    Input: texto (str) - String a ser verificada
    Output: (bool) - True se parece ser placa, False caso contrario
    """
    if not texto or not isinstance(texto, str):
        return False

    texto_limpo = re.sub(r'[^A-Z0-9]', '', texto.upper())

    if len(texto_limpo) != 7:
        return False

    padrao_antigo = re.match(r'^[A-Z]{3}[0-9]{4}$', texto_limpo)

    padrao_mercosul = re.match(r'^[A-Z]{3}[0-9]{1}[A-Z]{1}[0-9]{2}$', texto_limpo)

    return bool(padrao_antigo or padrao_mercosul)


def _obter_id_veiculo_por_placa(placa: str) -> Tuple[Optional[int], Optional[str]]:
    """
    Busca ID numerico de um veiculo pela placa usando API de verificacao

    Input: placa (str) - Placa do veiculo
    Output: (tuple) - (veiculo_id, mensagem_erro)
    """
    logger.info(f"[PLACA] Detectado que veiculo_id e uma placa: {placa}")
    logger.info("[PLACA] Buscando ID numerico do veiculo na API")

    try:
        autenticado, auth_ou_erro = autenticar_api()
        if not autenticado:
            return None, f"Erro de autenticacao ao buscar veiculo: {auth_ou_erro}"

        url = f"{API_BASE_URL}/publico/veiculo/v1/verificar-cadastro"

        placa_limpa = re.sub(r'[^A-Z0-9]', '', placa.upper())

        params = {'placa': placa_limpa}
        headers = {'Cookie': auth_cookie}

        logger.info(f"[API] Consultando API: {url}?placa={placa_limpa}")

        response = retry_on_timeout(
            lambda: requests.get(
                url,
                params=params,
                headers=headers,
                timeout=15
            ),
            max_retries=3,
            operation_name=f"Buscar veiculo por placa {placa_limpa}",
            telefone=None
        )

        logger.info(f"[API] Resposta recebida - Status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            veiculo_principal = data.get('veiculoCavaloOuCaminhao', {})
            veiculo_id = veiculo_principal.get('id')

            if veiculo_id:
                logger.info(f"[PLACA] Veiculo encontrado - ID numerico: {veiculo_id}")
                return int(veiculo_id), None
            else:
                return None, f"Veiculo com placa {placa} nao possui ID no sistema"

        elif response.status_code == 404:
            return None, f"Veiculo com placa {placa} nao encontrado. Cadastre o veiculo antes de criar o embarque."

        else:
            return None, f"Erro ao buscar veiculo pela placa: HTTP {response.status_code}"

    except requests.exceptions.Timeout:
        return None, "Timeout ao buscar veiculo pela placa"

    except Exception as e:
        logger.error(f"[PLACA] Erro ao buscar veiculo pela placa: {str(e)}", exc_info=True)
        return None, f"Erro ao buscar veiculo: {str(e)}"


def _converter_para_datetime_iso(data_str: str) -> str:
    """
    Converte string de data para formato ISO 8601 com horario

    Input: data_str (str) - String com data/datetime
    Output: (str) - String no formato ISO 8601 com timezone UTC
    """
    if not data_str:
        return _gerar_previsao_embarque()

    data_str = data_str.strip()

    if data_str.endswith('Z') and 'T' in data_str:
        return data_str

    if '/' in data_str:
        logger.info(f"[CONVERSAO] Detectado formato brasileiro: {data_str}")

        data_limpa = data_str.rstrip('Z').strip()

        partes = data_limpa.split(' ')
        data_parte = partes[0]
        hora_parte = partes[1] if len(partes) > 1 else "12:00"

        data_componentes = data_parte.split('/')
        if len(data_componentes) != 3:
            logger.error(f"[CONVERSAO] Formato de data invalido: {data_str}")
            return _gerar_previsao_embarque()

        try:
            dia = data_componentes[0].zfill(2)
            mes = data_componentes[1].zfill(2)
            ano = data_componentes[2]

            if ':' in hora_parte:
                hora_componentes = hora_parte.split(':')
                hora = hora_componentes[0].zfill(2)
                minuto = hora_componentes[1].zfill(2)
                segundo = hora_componentes[2].zfill(2) if len(hora_componentes) > 2 else "00"
            else:
                hora = "12"
                minuto = "00"
                segundo = "00"

            data_iso = f"{ano}-{mes}-{dia}T{hora}:{minuto}:{segundo}Z"
            logger.info(f"[CONVERSAO] Convertido de brasileiro '{data_str}' para ISO '{data_iso}'")
            return data_iso

        except (IndexError, ValueError) as e:
            logger.error(f"[CONVERSAO] Erro ao converter data brasileira: {data_str} - {e}")
            return _gerar_previsao_embarque()

    if len(data_str) == 10 and data_str.count('-') == 2:
        return f"{data_str}T12:00:00Z"

    if 'T' in data_str:
        return f"{data_str}Z" if not data_str.endswith('Z') else data_str

    logger.warning(f"[CONVERSAO] Formato de data nao reconhecido: {data_str}, gerando nova")
    return _gerar_previsao_embarque()


def _gerar_previsao_embarque() -> str:
    """
    Gera data/hora de previsao de embarque 24h a partir de agora em formato ISO 8601

    Input: None
    Output: (str) - Timestamp ISO 8601 com timezone UTC
    """
    agora = datetime.now(timezone.utc)
    previsao = agora + timedelta(hours=24)
    return previsao.strftime("%Y-%m-%dT%H:%M:%SZ")


def _obter_tipo_veiculo_por_id(veiculo_id: int, motorista_id: int) -> Tuple[Optional[str], Optional[str]]:
    """
    Busca tipo do veiculo pelo ID na tabela veiculos

    Input: veiculo_id (int) - ID do veiculo
           motorista_id (int) - ID do motorista (sort key)
    Output: (tuple) - (tipo_veiculo_nome, mensagem_erro)
    """
    if not veiculo_id:
        return None, "veiculo_id nao fornecido"

    if not motorista_id:
        return None, "motorista_id nao fornecido"

    try:
        logger.info(f"[VEICULO-TIPO] Buscando tipo do veiculo ID: {veiculo_id}, Motorista ID: {motorista_id}")

        response = veiculos_table.get_item(
            Key={
                'id_veiculo': str(veiculo_id),
                'id_motorista': str(motorista_id)
            },
            ProjectionExpression='tipo_veiculo_nome'
        )

        item = response.get('Item')

        if not item:
            logger.warning(f"[VEICULO-TIPO] Veiculo {veiculo_id} nao encontrado na tabela veiculos")
            return None, f"Veiculo {veiculo_id} nao encontrado"

        tipo_veiculo_nome = item.get('tipo_veiculo_nome')

        if not tipo_veiculo_nome:
            logger.warning(f"[VEICULO-TIPO] Veiculo {veiculo_id} nao possui tipo_veiculo_nome")
            return None, "Tipo de veiculo nao definido"

        logger.info(f"[VEICULO-TIPO] Tipo encontrado: {tipo_veiculo_nome}")
        return tipo_veiculo_nome, None

    except ClientError as e:
        error_code = e.response['Error']['Code']
        logger.error(f"[VEICULO-TIPO] Erro DynamoDB: {error_code}")
        return None, f"Erro ao buscar tipo de veiculo: {error_code}"

    except Exception as e:
        logger.error(f"[VEICULO-TIPO] Erro: {str(e)}", exc_info=True)
        return None, f"Erro ao buscar tipo de veiculo: {str(e)}"


def _obter_tipos_primeiro_equipamento(equipamento_id: int, veiculo_id: int) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Busca tipos do primeiro equipamento pela ID na tabela equipamentos

    Input: equipamento_id (int) - ID do equipamento
           veiculo_id (int) - ID do veiculo (sort key)
    Output: (tuple) - (tipo_veiculo_nome, tipo_equipamento_nome, mensagem_erro)
    """
    if not equipamento_id:
        return None, None, "equipamento_id nao fornecido"

    if not veiculo_id:
        return None, None, "veiculo_id nao fornecido"

    try:
        logger.info(f"[EQUIPAMENTO-TIPOS] Buscando tipos do equipamento ID: {equipamento_id}, Veiculo ID: {veiculo_id}")

        equipamentos_table = dynamodb.Table(EQUIPAMENTOS_TABLE)

        response = equipamentos_table.get_item(
            Key={
                'id_equipamento': str(equipamento_id),
                'id_veiculo': str(veiculo_id)
            },
            ProjectionExpression='tipo_veiculo_nome, tipo_equipamento_nome'
        )

        item = response.get('Item')

        if not item:
            logger.warning(f"[EQUIPAMENTO-TIPOS] Equipamento {equipamento_id} nao encontrado na tabela equipamentos")
            return None, None, f"Equipamento {equipamento_id} nao encontrado"

        tipo_veiculo_nome = item.get('tipo_veiculo_nome')
        tipo_equipamento_nome = item.get('tipo_equipamento_nome')

        if not tipo_veiculo_nome:
            logger.warning(f"[EQUIPAMENTO-TIPOS] Equipamento {equipamento_id} nao possui tipo_veiculo_nome")
            return None, None, "Tipo de veiculo do equipamento nao definido"

        if not tipo_equipamento_nome:
            logger.warning(f"[EQUIPAMENTO-TIPOS] Equipamento {equipamento_id} nao possui tipo_equipamento_nome")
            return None, None, "Tipo de equipamento nao definido"

        logger.info(f"[EQUIPAMENTO-TIPOS] Tipos encontrados - Veiculo: {tipo_veiculo_nome}, Equipamento: {tipo_equipamento_nome}")
        return tipo_veiculo_nome, tipo_equipamento_nome, None

    except ClientError as e:
        error_code = e.response['Error']['Code']
        logger.error(f"[EQUIPAMENTO-TIPOS] Erro DynamoDB: {error_code}")
        return None, None, f"Erro ao buscar tipos do equipamento: {error_code}"

    except Exception as e:
        logger.error(f"[EQUIPAMENTO-TIPOS] Erro: {str(e)}", exc_info=True)
        return None, None, f"Erro ao buscar tipos do equipamento: {str(e)}"


def _validar_tipo_veiculo_permitido(veiculo_id: int, motorista_id: int, tipos_permitidos: list) -> Tuple[bool, Optional[str]]:
    """
    Valida se tipo do veiculo esta na lista de tipos permitidos pela carga

    Input: veiculo_id (int) - ID do veiculo do motorista
           motorista_id (int) - ID do motorista
           tipos_permitidos (list) - Lista de tipos de veiculo permitidos
    Output: (tuple) - (eh_permitido, mensagem_erro)
    """
    if not tipos_permitidos:
        logger.warning("[VALIDACAO-VEICULO] Lista de tipos permitidos vazia - permitindo qualquer veiculo")
        return True, None

    tipo_veiculo, erro = _obter_tipo_veiculo_por_id(veiculo_id, motorista_id)

    if erro:
        logger.error(f"[VALIDACAO-VEICULO] Erro ao buscar tipo: {erro}")
        return False, f"Nao foi possivel verificar o tipo de veiculo: {erro}"

    if not tipo_veiculo:
        return False, "Tipo de veiculo nao encontrado"

    logger.info(f"[VALIDACAO-VEICULO] Verificando tipo '{tipo_veiculo}' contra lista: {tipos_permitidos}")

    if tipo_veiculo in tipos_permitidos:
        logger.info(f"[VALIDACAO-VEICULO] Tipo de veiculo '{tipo_veiculo}' e permitido")
        return True, None
    else:
        tipos_str = ", ".join(tipos_permitidos)
        mensagem = f"Tipo de veiculo '{tipo_veiculo}' nao permitido. Tipos aceitos: {tipos_str}"
        logger.warning(f"[VALIDACAO-VEICULO] {mensagem}")
        return False, mensagem


def _validar_tipo_veiculo_com_equipamento(
    veiculo_id: int,
    motorista_id: int,
    equipamentos: list,
    tipos_veiculo_permitidos: list,
    equipamentos_requeridos: list
) -> Tuple[bool, Optional[str]]:
    """
    Valida tipo de veiculo considerando equipamentos

    Input: veiculo_id (int) - ID do veiculo principal
           motorista_id (int) - ID do motorista
           equipamentos (list) - Lista de tuplas (indice, equipamento_id)
           tipos_veiculo_permitidos (list) - Lista de tipos permitidos
           equipamentos_requeridos (list) - Lista de equipamentos requeridos
    Output: (tuple) - (eh_permitido, mensagem_erro)
    """
    if not tipos_veiculo_permitidos:
        logger.warning("[VALIDACAO-VEICULO-EQ] Lista de tipos permitidos vazia - permitindo qualquer veiculo")
        return True, None

    if equipamentos_requeridos and len(equipamentos_requeridos) > 0:
        logger.info(f"[VALIDACAO-VEICULO-EQ] Carga requer equipamento: {equipamentos_requeridos}")

        if not equipamentos or len(equipamentos) == 0:
            logger.error("[VALIDACAO-VEICULO-EQ] Carga requer equipamento mas motorista nao possui equipamento cadastrado")
            return False, "Esta carga requer equipamento, mas voce nao possui equipamento cadastrado"

        primeiro_idx, primeiro_eq_id = equipamentos[0]
        logger.info(f"[VALIDACAO-VEICULO-EQ] Usando PRIMEIRO equipamento: ID={primeiro_eq_id}, Indice={primeiro_idx}")

        tipo_veiculo_eq, tipo_equipamento_eq, erro = _obter_tipos_primeiro_equipamento(primeiro_eq_id, veiculo_id)

        if erro:
            logger.error(f"[VALIDACAO-VEICULO-EQ] Erro ao buscar tipos do equipamento: {erro}")
            return False, f"Nao foi possivel verificar o tipo do equipamento: {erro}"

        if not tipo_veiculo_eq or not tipo_equipamento_eq:
            logger.error("[VALIDACAO-VEICULO-EQ] Tipos do equipamento nao encontrados")
            return False, "Tipos do equipamento nao encontrados"

        logger.info(f"[VALIDACAO-VEICULO-EQ] Validando tipo de veiculo do equipamento: '{tipo_veiculo_eq}' contra lista: {tipos_veiculo_permitidos}")

        if tipo_veiculo_eq not in tipos_veiculo_permitidos:
            tipos_str = ", ".join(tipos_veiculo_permitidos)
            mensagem = f"Tipo de veiculo do equipamento '{tipo_veiculo_eq}' nao permitido. Tipos aceitos: {tipos_str}"
            logger.warning(f"[VALIDACAO-VEICULO-EQ] {mensagem}")
            return False, mensagem

        logger.info(f"[VALIDACAO-VEICULO-EQ] Validando tipo de equipamento: '{tipo_equipamento_eq}' contra lista: {equipamentos_requeridos}")

        if tipo_equipamento_eq not in equipamentos_requeridos:
            equipamentos_str = ", ".join(equipamentos_requeridos)
            mensagem = f"Tipo de equipamento '{tipo_equipamento_eq}' nao permitido. Tipos aceitos: {equipamentos_str}"
            logger.warning(f"[VALIDACAO-VEICULO-EQ] {mensagem}")
            return False, mensagem

        logger.info(f"[VALIDACAO-VEICULO-EQ] Equipamento valido - Veiculo: '{tipo_veiculo_eq}', Equipamento: '{tipo_equipamento_eq}'")
        return True, None

    else:
        logger.info("[VALIDACAO-VEICULO-EQ] Carga nao requer equipamento - validando veiculo principal")
        return _validar_tipo_veiculo_permitido(veiculo_id, motorista_id, tipos_veiculo_permitidos)


def _validar_periodo_disponibilidade(previsao_embarque: str, inicio_periodo: str, fim_periodo: str) -> Tuple[bool, Optional[str]]:
    """
    Valida se data de previsao de embarque esta dentro do periodo de disponibilidade da carga

    Input: previsao_embarque (str) - Data de previsao em ISO 8601
           inicio_periodo (str) - Data inicial no formato YYYY-MM-DD
           fim_periodo (str) - Data final no formato YYYY-MM-DD
    Output: (tuple) - (esta_no_periodo, mensagem_erro)
    """
    try:
        logger.info(f"[VALIDACAO-PERIODO] Validando previsao '{previsao_embarque}' contra periodo {inicio_periodo} a {fim_periodo}")

        if 'T' in previsao_embarque:
            data_embarque_str = previsao_embarque.split('T')[0]
        else:
            data_embarque_str = previsao_embarque[:10]

        data_embarque = datetime.strptime(data_embarque_str, "%Y-%m-%d").date()
        data_inicio = datetime.strptime(inicio_periodo, "%Y-%m-%d").date()
        data_fim = datetime.strptime(fim_periodo, "%Y-%m-%d").date()

        logger.info(f"[VALIDACAO-PERIODO] Data embarque: {data_embarque}, Inicio: {data_inicio}, Fim: {data_fim}")

        if data_inicio <= data_embarque <= data_fim:
            logger.info("[VALIDACAO-PERIODO] Data de embarque esta dentro do periodo")
            return True, None
        else:
            mensagem = f"Data de embarque ({data_embarque_str}) fora do periodo de disponibilidade ({inicio_periodo} a {fim_periodo})"
            logger.warning(f"[VALIDACAO-PERIODO] {mensagem}")
            return False, mensagem

    except ValueError as e:
        mensagem = f"Erro ao processar datas: {str(e)}"
        logger.error(f"[VALIDACAO-PERIODO] {mensagem}")
        return False, mensagem

    except Exception as e:
        mensagem = f"Erro ao validar periodo: {str(e)}"
        logger.error(f"[VALIDACAO-PERIODO] {mensagem}", exc_info=True)
        return False, mensagem


def _obter_valor(params: Dict, session: Dict, possible_names: list, default: Any = None) -> Any:
    """
    Obtem valor priorizando parameters sobre session attributes

    Input: params (dict) - Parametros do action group
           session (dict) - Atributos da sessao
           possible_names (list) - Lista de nomes possiveis em ordem de prioridade
           default (Any) - Valor padrao se nao encontrado
    Output: (Any) - Valor encontrado ou default
    """
    if isinstance(possible_names, str):
        possible_names = [possible_names]

    for name in possible_names:
        valor = params.get(name)
        if valor is not None and valor != '':
            logger.info(f"[VALIDACAO] Valor obtido dos parametros - key: {name}")
            return valor

    for name in possible_names:
        valor = session.get(name)
        if valor is not None and valor != '':
            logger.info(f"[VALIDACAO] Valor obtido da sessao - key: {name}")
            return valor

    logger.info(f"[VALIDACAO] Valor nao encontrado - Nomes buscados: {possible_names}, usando default: {default}")
    return default


def criar_embarque(params: Dict, session: Dict) -> Dict[str, Any]:
    """
    Cria embarque na API Rodosafra com validacoes de tipo de veiculo e periodo

    Input: params (dict) - Parametros da funcao com dados do embarque
           session (dict) - Atributos da sessao com dados adicionais
    Output: (dict) - Status da criacao, ID do embarque e payload completo da API

    Retorno em caso de sucesso:
    {
        "status": "sucesso",
        "mensagem": "Embarque criado com sucesso",
        "embarque_id": <id>,
        "dados_enviados": {...},  # Dados que foram enviados para a API
        "api_response": {...}     # Payload completo retornado pela API (inclui data real cadastrada)
    }

    O campo api_response permite que o chatbot veja os dados reais cadastrados pela API,
    incluindo ajustes de data/hora feitos pelo backend.
    """
    logger.info("[EMBARQUE] Iniciando criacao de embarque")
    logger.info("[EMBARQUE] Prioridade de dados: Banco (negociacao) > Parameters > Session attributes")

    autenticado, auth_ou_erro = autenticar_api()
    if not autenticado:
        logger.error(f"[AUTH] Falha na autenticacao: {auth_ou_erro}")
        return {
            "status": "erro",
            "mensagem": f"Erro de autenticacao: {auth_ou_erro}"
        }

    telefone = _obter_valor(params, session, ['telefone', 'motorista_telefone', 'telefone_motorista'])

    if not telefone:
        logger.warning("[EMBARQUE] Telefone nao disponivel - nao sera possivel buscar dados no banco")

    motorista_id_str = _obter_valor(params, session, ['motorista_id', 'id_motorista'])

    if not motorista_id_str:
        logger.error("[VALIDACAO] ID do motorista nao fornecido")
        return {
            "status": "erro",
            "mensagem": "ID do motorista nao fornecido",
            "detalhes": "Campo obrigatorio: motorista_id"
        }

    try:
        motorista_id = int(motorista_id_str)
    except (ValueError, TypeError):
        logger.error(f"[VALIDACAO] ID do motorista invalido: {motorista_id_str}")
        return {
            "status": "erro",
            "mensagem": f"ID do motorista invalido: {motorista_id_str}"
        }

    cavalo_id = None
    cavalo_id_origem = "desconhecida"

    if telefone:
        logger.info("[EMBARQUE] Tentando buscar veiculo_cavalo_id do banco negociacao")
        veiculo_db, _, erro_db = _buscar_veiculo_e_equipamentos_por_telefone(telefone)

        if veiculo_db:
            cavalo_id = veiculo_db
            cavalo_id_origem = "banco_negociacao"
            logger.info(f"[EMBARQUE] veiculo_cavalo_id obtido do BANCO: {cavalo_id}")
        elif erro_db:
            logger.warning(f"[EMBARQUE] Erro ao buscar no banco: {erro_db}")

    if not cavalo_id:
        logger.info("[EMBARQUE] veiculo_cavalo_id nao encontrado no banco, usando parameters/session")
        cavalo_id_str = _obter_valor(params, session, ['veiculo_id', 'veiculo_cavalo_id', 'cavalo_id', 'veiculo_principal_id'])

        if not cavalo_id_str:
            logger.error("[VALIDACAO] ID do veiculo cavalo/caminhao nao fornecido e nao encontrado no banco")
            return {
                "status": "erro",
                "mensagem": "ID do veiculo cavalo/caminhao nao fornecido e nao encontrado no banco",
                "detalhes": "Campo obrigatorio: veiculo_cavalo_id"
            }

        if _eh_placa_brasileira(cavalo_id_str):
            logger.warning(f"[VALIDACAO] veiculo_id recebido como placa ({cavalo_id_str}), buscando ID numerico")

            veiculo_id_numerico, erro_busca = _obter_id_veiculo_por_placa(cavalo_id_str)

            if erro_busca:
                logger.error(f"[VALIDACAO] Erro ao obter ID do veiculo: {erro_busca}")
                return {
                    "status": "erro",
                    "mensagem": f"Erro ao obter ID do veiculo: {erro_busca}",
                    "detalhes": f"O chatbot forneceu placa ({cavalo_id_str}) ao inves do ID numerico. O sistema tentou buscar automaticamente mas falhou.",
                    "sugestao": "Verifique se o veiculo esta cadastrado corretamente"
                }

            cavalo_id = veiculo_id_numerico
            cavalo_id_origem = "params_via_placa"
            logger.info(f"[EMBARQUE] ID numerico obtido de placa: {cavalo_id}")
        else:
            try:
                cavalo_id = int(cavalo_id_str)
                cavalo_id_origem = "params"
                logger.info(f"[EMBARQUE] veiculo_cavalo_id obtido de parameters/session: {cavalo_id}")
            except (ValueError, TypeError):
                logger.error(f"[VALIDACAO] ID do veiculo cavalo invalido: {cavalo_id_str}")
                return {
                    "status": "erro",
                    "mensagem": f"ID do veiculo cavalo invalido: {cavalo_id_str}",
                    "detalhes": "O ID deve ser um numero inteiro"
                }

    logger.info(f"[EMBARQUE] veiculo_cavalo_id final: {cavalo_id} (origem: {cavalo_id_origem})")

    carga_id_str = _obter_valor(params, session, ['carga_id', 'carga_id_selecionada', 'oferta_id'])

    if not carga_id_str:
        logger.info("[EMBARQUE] carga_id nao fornecido, tentando buscar no DynamoDB")

        telefone = _obter_valor(params, session, ['telefone', 'motorista_telefone', 'telefone_motorista'])

        if not telefone:
            logger.error("[VALIDACAO] Nao foi possivel buscar carga_id: telefone nao disponivel")
            return {
                "status": "erro",
                "mensagem": "ID da carga nao fornecido e nao foi possivel buscar automaticamente",
                "detalhes": "Para busca automatica, e necessario ter o telefone do motorista nos session attributes"
            }

        carga_id_auto, erro_busca = _buscar_carga_id_por_telefone(telefone)

        if erro_busca or not carga_id_auto:
            logger.error(f"[VALIDACAO] Falha na busca automatica de carga_id: {erro_busca}")
            return {
                "status": "erro",
                "mensagem": "ID da carga nao fornecido e nao foi possivel buscar automaticamente",
                "detalhes": erro_busca or "Nenhuma oferta encontrada para o motorista"
            }

        carga_id = carga_id_auto
        logger.info(f"[EMBARQUE] carga_id obtido automaticamente do DynamoDB: {carga_id}")
    else:
        try:
            carga_id = int(carga_id_str)
        except (ValueError, TypeError):
            logger.error(f"[VALIDACAO] ID da carga invalido: {carga_id_str}")
            return {
                "status": "erro",
                "mensagem": f"ID da carga invalido: {carga_id_str}"
            }

    logger.info(f"[EMBARQUE] IDs validados - Motorista: {motorista_id}, Cavalo: {cavalo_id}, Carga: {carga_id}")

    peso_estimado_str = _obter_valor(params, session, ['peso_estimado', 'peso'], '30.0')

    try:
        peso_estimado = float(peso_estimado_str)
        if peso_estimado <= 0:
            peso_estimado = 30.0
    except (ValueError, TypeError):
        logger.warning(f"[VALIDACAO] Peso invalido: {peso_estimado_str}, usando padrao 30.0")
        peso_estimado = 30.0

    previsao_embarque_str = _obter_valor(params, session, ['previsao_embarque', 'embarque_previsao_carregamento', 'data_previsao'])

    if previsao_embarque_str:
        previsao_embarque = _converter_para_datetime_iso(previsao_embarque_str)
    else:
        previsao_embarque = _gerar_previsao_embarque()

    logger.info(f"[EMBARQUE] Peso estimado: {peso_estimado}, Previsao: {previsao_embarque}")

    equipamentos = []
    equipamentos_origem = "nenhum"

    if telefone:
        logger.info("[EMBARQUE] Tentando buscar equipamento_ids do banco negociacao")
        _, equipamentos_db, erro_db = _buscar_veiculo_e_equipamentos_por_telefone(telefone)

        if equipamentos_db:
            logger.info(f"[EMBARQUE] Encontrados {len(equipamentos_db)} equipamentos no BANCO negociacao")
            for idx, equip_id in enumerate(equipamentos_db, 1):
                if idx <= 3:
                    equipamentos.append((idx, equip_id))
                    logger.info(f"[EMBARQUE] Equipamento {idx} do BANCO: {equip_id}")
            equipamentos_origem = "banco_negociacao"
        elif erro_db:
            logger.warning(f"[EMBARQUE] Erro ao buscar no banco: {erro_db}")

    if equipamentos:
        logger.info(f"[EMBARQUE] Usando {len(equipamentos)} equipamentos do BANCO - ignorando parameters/session")
    else:
        logger.info("[EMBARQUE] Nenhum equipamento encontrado no banco, tentando buscar na tabela equipamentos")

        equipamentos_db_ids = _obter_equipamentos_por_veiculo_id(cavalo_id, telefone)

        if equipamentos_db_ids:
            logger.info(f"[EMBARQUE] Encontrados {len(equipamentos_db_ids)} equipamentos na tabela equipamentos")
            for idx, equip_id in enumerate(equipamentos_db_ids, 1):
                if idx <= 3:
                    equipamentos.append((idx, equip_id))
                    logger.info(f"[EMBARQUE] Equipamento {idx} (tabela equipamentos): {equip_id}")
            equipamentos_origem = "tabela_equipamentos"

    if not equipamentos:
        logger.info("[EMBARQUE] Nenhum equipamento encontrado nas tabelas, tentando parameters/session")

        equipamentos_ids_param = _obter_valor(params, session, ['equipamentos_ids', 'equipamento_ids'])

        if equipamentos_ids_param:
            logger.info(f"[EMBARQUE] Parametro equipamentos_ids recebido: {equipamentos_ids_param}")

            placas_ou_ids = []

            if isinstance(equipamentos_ids_param, str):
                equipamentos_ids_param = equipamentos_ids_param.strip('[]"\'')
                placas_ou_ids = [item.strip() for item in equipamentos_ids_param.split(',')]
            elif isinstance(equipamentos_ids_param, list):
                placas_ou_ids = equipamentos_ids_param

            logger.info(f"[EMBARQUE] Placas/IDs parseados: {placas_ou_ids}")

            placas_detectadas = []
            ids_detectados = []

            for item in placas_ou_ids:
                item_str = str(item).strip()

                if _eh_placa_brasileira(item_str):
                    placas_detectadas.append(item_str)
                else:
                    try:
                        ids_detectados.append(int(item_str))
                    except (ValueError, TypeError):
                        logger.warning(f"[EMBARQUE] Item invalido (nao e placa nem ID): {item_str}")

            if placas_detectadas:
                logger.info(f"[EMBARQUE] Detectadas {len(placas_detectadas)} placas, buscando IDs")
                ids_por_placa = _obter_equipamentos_por_placas(placas_detectadas)
                ids_detectados.extend(ids_por_placa)

            if ids_detectados:
                logger.info(f"[EMBARQUE] Usando {len(ids_detectados)} equipamentos dos parametros")
                for idx, equip_id in enumerate(ids_detectados, 1):
                    if idx <= 3:
                        equipamentos.append((idx, equip_id))
                        logger.info(f"[EMBARQUE] Equipamento {idx} (dos params): {equip_id}")
                equipamentos_origem = "params_lista"

        if not equipamentos_ids_param:
            equipamentos_individuais = []

            for i in range(1, 4):
                equip_id_str = _obter_valor(params, session, [
                    f'veiculo_equipamento_{i}_id',
                    f'equipamento_{i}_id',
                    f'veiculo_equipamento{i}_id',
                    f'equipamento{i}_id'
                ])

                if equip_id_str:
                    if _eh_placa_brasileira(equip_id_str):
                        logger.warning(f"[VALIDACAO] equipamento_{i}_id recebido como placa ({equip_id_str}), buscando ID numerico")

                        ids_encontrados = _obter_equipamentos_por_placas([equip_id_str])

                        if ids_encontrados:
                            equip_id = ids_encontrados[0]
                            logger.info(f"[EMBARQUE] Equipamento {i} - ID numerico obtido: {equip_id}")
                        else:
                            logger.error(f"[EMBARQUE] Equipamento {i} nao encontrado pela placa: {equip_id_str}")
                            continue
                    else:
                        try:
                            equip_id = int(equip_id_str)
                        except (ValueError, TypeError):
                            logger.warning(f"[VALIDACAO] ID de equipamento {i} invalido: {equip_id_str}")
                            continue

                    equipamentos_individuais.append((i, equip_id))
                    logger.info(f"[EMBARQUE] Equipamento {i} (individual): {equip_id}")

            if equipamentos_individuais:
                logger.info(f"[EMBARQUE] Usando {len(equipamentos_individuais)} equipamentos individuais dos parametros")
                equipamentos = equipamentos_individuais
                equipamentos_origem = "params_individuais"

    logger.info(f"[EMBARQUE] Total final: {len(equipamentos)} equipamentos (origem: {equipamentos_origem})")

    logger.info(f"[VALIDACOES] Buscando dados da oferta {carga_id} para validacoes")

    try:
        response = ofertas_table.get_item(
            Key={'id_oferta': str(carga_id)},
            ProjectionExpression='veiculo, inicio_periodo, fim_periodo'
        )

        oferta_item = response.get('Item')

        if oferta_item:
            veiculo_oferta = oferta_item.get('veiculo', {})
            tipos_permitidos = veiculo_oferta.get('tipos', [])
            equipamentos_requeridos = veiculo_oferta.get('equipamentos', [])
            inicio_periodo = oferta_item.get('inicio_periodo')
            fim_periodo = oferta_item.get('fim_periodo')

            logger.info(f"[VALIDACOES] Oferta encontrada - Tipos permitidos: {tipos_permitidos}, Equipamentos requeridos: {equipamentos_requeridos}")

            if tipos_permitidos:
                logger.info("[VALIDACOES] Validando tipo de veiculo/equipamento")
                tipo_permitido, erro_tipo = _validar_tipo_veiculo_com_equipamento(
                    cavalo_id,
                    motorista_id,
                    equipamentos,
                    tipos_permitidos,
                    equipamentos_requeridos
                )

                if not tipo_permitido:
                    logger.error(f"[VALIDACOES] Validacao de tipo de veiculo/equipamento falhou: {erro_tipo}")
                    return {
                        "status": "erro",
                        "mensagem": "Tipo de veiculo/equipamento incompativel com a carga",
                        "detalhes": erro_tipo,
                        "tipo_erro": "validacao_tipo_veiculo"
                    }
            else:
                logger.info("[VALIDACOES] Lista de tipos permitidos vazia - pulando validacao de tipo")

            if inicio_periodo and fim_periodo:
                logger.info("[VALIDACOES] Validando periodo de disponibilidade")
                periodo_valido, erro_periodo = _validar_periodo_disponibilidade(
                    previsao_embarque,
                    inicio_periodo,
                    fim_periodo
                )

                if not periodo_valido:
                    logger.error(f"[VALIDACOES] Validacao de periodo falhou: {erro_periodo}")
                    return {
                        "status": "erro",
                        "mensagem": "Data de embarque fora do periodo de disponibilidade",
                        "detalhes": erro_periodo,
                        "tipo_erro": "validacao_periodo"
                    }
            else:
                logger.info("[VALIDACOES] Periodo nao definido na oferta - pulando validacao de periodo")

            logger.info("[VALIDACOES] Todas as validacoes passaram")

        else:
            logger.warning(f"[VALIDACOES] Oferta {carga_id} nao encontrada na tabela ofertas - pulando validacoes")

    except ClientError as e:
        error_code = e.response['Error']['Code']
        logger.error(f"[VALIDACOES] Erro DynamoDB ao buscar oferta: {error_code}")
        logger.warning("[VALIDACOES] Continuando sem validacoes devido a erro de busca")

    except Exception as e:
        logger.error(f"[VALIDACOES] Erro ao buscar oferta: {str(e)}", exc_info=True)
        logger.warning("[VALIDACOES] Continuando sem validacoes devido a erro inesperado")

    payload = {
        "cargaId": carga_id,
        "motoristaId": motorista_id,
        "veiculoCavaloOuCaminhaoId": cavalo_id,
        "pesoEstimadoEmbarque": peso_estimado,
        "previsaoEmbarque": previsao_embarque
    }

    for idx, equip_id in equipamentos:
        payload[f"veiculoEquipamento{idx}Id"] = equip_id

    logger.info(f"[EMBARQUE] Payload final preparado com {len(payload)} campos")

    telefone_session = session.get('telefone') or session.get('conversa_id')

    try:
        url = f"{API_BASE_URL}/publico/embarque"

        logger.info(f"[API] Chamando endpoint de criacao de embarque")
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
            operation_name="Criar embarque",
            telefone=telefone_session
        )

        logger.info(f"[API] Resposta recebida - Status: {response.status_code}")

        if response.status_code in [200, 201, 204]:
            embarque_id = None
            api_response_data = None

            try:
                if response.text:
                    api_response_data = response.json()

                    # Tenta extrair o ID do embarque de diferentes formatos possíveis
                    if isinstance(api_response_data, (int, str)):
                        embarque_id = api_response_data
                    elif isinstance(api_response_data, dict):
                        embarque_id = api_response_data.get('id') or api_response_data.get('embarqueId') or api_response_data.get('embarque_id')

                    logger.info(f"[EMBARQUE] Embarque criado com sucesso - ID: {embarque_id}")
                    logger.info(f"[EMBARQUE] Payload completo da API: {api_response_data}")
            except Exception as e:
                logger.info(f"[EMBARQUE] Embarque criado (sem ID no response): {e}")

            result = {
                "status": "sucesso",
                "mensagem": "Embarque criado com sucesso",
                "embarque_id": embarque_id,
                "dados_enviados": {
                    "motorista_id": motorista_id,
                    "carga_id": carga_id,
                    "cavalo_id": cavalo_id,
                    "equipamentos": [eq_id for _, eq_id in equipamentos],
                    "peso_estimado": peso_estimado,
                    "previsao_embarque": previsao_embarque
                }
            }

            # Inclui o payload da API para que o chatbot possa usar os dados reais
            if api_response_data:
                result["api_response"] = api_response_data
                logger.info("[EMBARQUE] Payload da API incluído no retorno para o chatbot")

            # Verifica se motorista tem flag de duvida sobre embarque ativa
            logger.info("[FLAG_DUVIDA] Verificando se motorista tem duvidas sobre embarque...")
            try:
                motorista_response = motoristas_table.get_item(Key={'id_motorista': motorista_id})
                motorista_item = motorista_response.get('Item', {})

                duvida_embarque_flag = motorista_item.get('duvida_embarque', False)
                logger.info(f"[FLAG_DUVIDA] Flag duvida_embarque para motorista {motorista_id}: {duvida_embarque_flag}")

                if duvida_embarque_flag is True:
                    logger.info("[FLAG_DUVIDA] Flag ativa - iniciando transbordo apos embarque...")

                    # Obtem telefone do motorista para o transbordo
                    telefone_motorista = motorista_item.get('telefone') or session.get('telefone')

                    if telefone_motorista:
                        logger.info(f"[FLAG_DUVIDA] Acionando transbordo para telefone: {telefone_motorista}")

                        # Chama o transbordo com setor logistica e motivo especifico
                        transbordo_resultado = executar_transbordo(
                            telefone=telefone_motorista,
                            setor="logistica",
                            motivo="duvida_local_embarque"
                        )

                        logger.info(f"[FLAG_DUVIDA] Resultado do transbordo: {transbordo_resultado.get('status')}")

                        # Reseta a flag no banco de dados
                        motoristas_table.update_item(
                            Key={'id_motorista': motorista_id},
                            UpdateExpression='SET duvida_embarque = :false, updated_at = :timestamp',
                            ExpressionAttributeValues={
                                ':false': False,
                                ':timestamp': datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                            }
                        )
                        logger.info("[FLAG_DUVIDA] Flag resetada com sucesso")

                        # Adiciona informação sobre o transbordo no resultado
                        result["transbordo_acionado"] = True
                        result["transbordo_motivo"] = "duvida_local_embarque"
                        result["instrucao_chatbot"] = "Embarque criado com sucesso. Transbordo foi acionado automaticamente devido a duvidas sobre o local de embarque. Informe ao motorista que em breve alguem do time entrara em contato."
                    else:
                        logger.warning("[FLAG_DUVIDA] Telefone do motorista nao encontrado - transbordo nao acionado")
                        result["transbordo_acionado"] = False
                        result["transbordo_erro"] = "telefone_nao_encontrado"
                else:
                    logger.info("[FLAG_DUVIDA] Flag nao esta ativa - transbordo nao sera acionado")
                    result["transbordo_acionado"] = False

            except Exception as flag_error:
                logger.error(f"[FLAG_DUVIDA] Erro ao verificar/processar flag de duvida: {str(flag_error)}", exc_info=True)
                # Não falha o embarque por causa disso - apenas loga o erro
                result["transbordo_acionado"] = False
                result["transbordo_erro"] = str(flag_error)[:200]

            return result

        elif response.status_code == 400:
            try:
                erro_api = response.json()
                mensagem_erro = erro_api.get('mensagem', erro_api.get('message', 'Erro de validacao'))
            except:
                mensagem_erro = response.text[:200]

            logger.error(f"[API] Erro 400 - Validacao: {mensagem_erro}")

            return {
                "status": "erro",
                "mensagem": f"Dados invalidos: {mensagem_erro}",
                "tipo_erro": "validacao"
            }

        elif response.status_code == 404:
            logger.error("[API] Motorista ou veiculos nao encontrados")

            return {
                "status": "erro",
                "mensagem": "Motorista ou veiculos nao encontrados na Rodosafra",
                "tipo_erro": "nao_encontrado",
                "sugestao": "Verifique se o cadastro foi concluido corretamente"
            }

        elif response.status_code == 500:
            logger.error("[API] Erro interno no servidor")

            log_api_error(
                api_route="/publico/embarque",
                error_code=500,
                error_message="Erro interno no servidor ao criar embarque",
                payload=payload,
                response_body=response.text
            )

            return {
                "status": "erro",
                "mensagem": "Erro interno no servidor - tente novamente em alguns instantes",
                "tipo_erro": "servidor"
            }

        else:
            logger.error(f"[API] Status inesperado: {response.status_code}")

            if response.status_code >= 500:
                log_api_error(
                    api_route="/publico/embarque",
                    error_code=response.status_code,
                    error_message=f"Erro inesperado ao criar embarque (HTTP {response.status_code})",
                    payload=payload,
                    response_body=response.text
                )

            return {
                "status": "erro",
                "mensagem": f"Erro ao criar embarque (HTTP {response.status_code})",
                "detalhe": response.text[:200] if response.text else "",
                "tipo_erro": "http"
            }

    except requests.exceptions.Timeout:
        logger.error("[API] Timeout na requisicao")
        return {
            "status": "erro",
            "mensagem": "Timeout ao criar embarque - tente novamente",
            "tipo_erro": "timeout"
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"[API] Erro na requisicao: {str(e)}", exc_info=True)
        return {
            "status": "erro",
            "mensagem": f"Erro ao conectar com API: {str(e)}",
            "tipo_erro": "conexao"
        }

    except Exception as e:
        logger.error(f"[ERRO] Erro inesperado: {str(e)}", exc_info=True)
        return {
            "status": "erro",
            "mensagem": f"Erro inesperado: {str(e)}",
            "tipo_erro": "inesperado"
        }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Handler principal do Lambda para action group de criacao de embarque

    Input: event (dict) - Evento do Bedrock Agent com parametros e sessao
           context (Any) - Contexto do Lambda
    Output: (dict) - Resposta formatada para Bedrock Agent
    """
    logger.info(f"[HANDLER] Event: {json.dumps(event, ensure_ascii=False)}")
    logger.info("[HANDLER] Iniciando action group - Criar Embarque")

    action_group = event.get('actionGroup', 'CriarEmbarque')
    function_name = event.get('function', 'criar_embarque')

    try:
        parameters = {p.get('name'): p.get('value') for p in event.get('parameters', [])}
        session_attributes = event.get('sessionAttributes', {})

        logger.info(f"[HANDLER] Funcao: {function_name}")
        logger.info(f"[HANDLER] Atributos de sessao disponiveis: {list(session_attributes.keys())}")

        if function_name == 'criar_embarque':
            resultado = criar_embarque(parameters, session_attributes)
        else:
            logger.warning(f"[HANDLER] Funcao desconhecida: {function_name}")
            resultado = {
                "status": "erro",
                "mensagem": f"Funcao desconhecida: {function_name}. Use criar_embarque"
            }

        logger.info(f"[HANDLER] Processamento concluido - Status: {resultado.get('status')}")

    except Exception as e:
        logger.error(f"[ERRO] Excecao critica no handler: {str(e)}", exc_info=True)

        resultado = {
            "status": "erro",
            "mensagem": "Ocorreu um erro ao criar o embarque. Por favor, tente novamente.",
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
