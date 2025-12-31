"""
Lambda para responder mensagens de motoristas via WebSocket

Input: Mensagens de motoristas recebidas via WebSocket, EventBridge ou transicoes SSE->WebSocket
Output: Respostas processadas pelo chatbot e enviadas de volta aos motoristas

Funcionalidades:
- Processamento de mensagens de motoristas com debouncing (acumulacao)
- Suporte a transicoes SSE->WebSocket (busca mensagens nao lidas e reconstroi contexto)
- Reconstrucao de contexto a partir do historico de mensagens
- Suporte a multiplos tipos de mensagem (texto, audio, imagem, documento, localizacao)
"""

import json
import base64
import os
import urllib3
import urllib
from typing import Dict, Any, Optional
import boto3
from botocore.exceptions import ClientError
import time
from datetime import datetime, timezone
from decimal import Decimal
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../requests/handler'))
try:
    from kpi_tracker import increment_responses
except ImportError:
    logger.warning("[IMPORT] KPI tracker nao disponivel, rastreamento de KPI desabilitado")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../chatbot/action-groups'))
from api_retry_util import retry_on_timeout

sys.path.insert(0, os.path.dirname(__file__))
from ecs_ip_resolver import get_websocket_url_with_fallback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../auxiliares'))
from message_history import (
    save_driver_message,
    save_chatbot_response,
    build_context_from_history
)

bedrock = boto3.client('bedrock-runtime')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
http = urllib3.PoolManager()

dynamodb = boto3.client("dynamodb")
dynamodb_resource = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("MOTORISTAS_TABLE")
SESSOES_TABLE_NAME = os.environ.get("MOTORISTAS_SESSOES_TABLE")
NEGOCIACAO_TABLE = os.environ.get("NEGOCIACAO_TABLE", "negociacao")
s3 = boto3.client("s3")
lambda_client = boto3.client("lambda")

ssm_client = boto3.client("ssm")

PARAMETER_STORE_TOKEN_NAME = os.environ.get(
    "PARAMETER_STORE_TOKEN_NAME", "/rodosafra/auth/token"
)

auth_cookie_cache = None

MESSAGE_ACCUMULATION_DELAY = int(os.environ.get("MESSAGE_ACCUMULATION_DELAY", "10"))
MESSAGE_EXTENSION_DELAY = int(os.environ.get("MESSAGE_EXTENSION_DELAY", "3"))

def sanitizar_texto_resposta(texto: str) -> str:
    """
    Sanitiza texto da resposta do chatbot convertendo escape sequences literais para caracteres reais

    Input: texto (str) - Texto da resposta do chatbot
    Output: (str) - Texto sanitizado com escape sequences convertidos
    """
    if not texto:
        return texto

    texto_sanitizado = texto.replace('\\n', '\n')
    texto_sanitizado = texto_sanitizado.replace('\\t', '\t')
    texto_sanitizado = texto_sanitizado.replace('\\r', '\r')

    logger.info(f"[SANITIZE] Texto sanitizado: {len(texto)} chars -> {len(texto_sanitizado)} chars")

    return texto_sanitizado

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

# ===== FUNÇÕES DE GERENCIAMENTO DE SESSÃO COM EXPIRAÇÃO =====

def _get_session_timestamp_from_negociacao(telefone: str) -> tuple:
    """
    Busca tempo_sessao e session_id mais recentes da tabela negociacao

    Input: telefone (str) - Telefone do motorista
    Output: (str, str) - Tupla com tempo_sessao e session_id, ou (None, None) se nao encontrado
    """
    if not telefone:
        logger.error("[SESSION] Telefone vazio ao buscar tempo_sessao")
        return None, None

    try:
        from boto3.dynamodb.conditions import Key

        negociacao_table = dynamodb_resource.Table(NEGOCIACAO_TABLE)

        response = negociacao_table.query(
            KeyConditionExpression=Key('telefone').eq(telefone),
            ScanIndexForward=False,
            Limit=1,
            ProjectionExpression='tempo_sessao, session_id'
        )

        items = response.get('Items', [])

        if items and 'tempo_sessao' in items[0]:
            tempo_sessao = str(items[0]['tempo_sessao'])
            session_id = str(items[0].get('session_id', '')) if items[0].get('session_id') else None
            logger.info(f"[SESSION] Encontrado na negociacao para {telefone}: tempo_sessao={tempo_sessao}, session_id={session_id}")
            return tempo_sessao, session_id
        else:
            logger.info(f"[SESSION] Nenhum tempo_sessao encontrado na negociacao para {telefone}")
            return None, None

    except Exception as e:
        logger.error(f"[SESSION] Erro ao buscar tempo_sessao da negociacao: {str(e)}")
        import traceback
        traceback.print_exc()
        return None, None


def _parse_timestamp_to_unix(tempo_str: str) -> int:
    """
    Converte timestamp para Unix timestamp em segundos

    Input: tempo_str (str) - Timestamp em formato ISO 8601, compacto ou Unix
    Output: (int) - Unix timestamp em segundos
    """
    if not tempo_str:
        raise ValueError("Timestamp string vazio")

    tempo_str = tempo_str.strip()

    if 'T' in tempo_str and '-' in tempo_str:
        try:
            if tempo_str.endswith('Z'):
                tempo_str = tempo_str[:-1]

            dt_obj = datetime.fromisoformat(tempo_str)

            if dt_obj.tzinfo is None:
                dt_obj = dt_obj.replace(tzinfo=timezone.utc)

            return int(dt_obj.timestamp())
        except ValueError as e:
            logger.error(f"[TIMESTAMP] Erro ao parsear formato ISO: {e}")
            raise

    if len(tempo_str) <= 10:
        return int(tempo_str)

    year = int(tempo_str[0:4])
    month = int(tempo_str[4:6])
    day = int(tempo_str[6:8])
    hour = int(tempo_str[8:10])
    minute = int(tempo_str[10:12])
    second = int(tempo_str[12:14])
    microsecond = int(tempo_str[14:20]) if len(tempo_str) >= 20 else 0

    dt_obj = datetime(year, month, day, hour, minute, second, microsecond, tzinfo=timezone.utc)
    return int(dt_obj.timestamp())

def _is_session_valid(tempo_sessao_str: str) -> bool:
    """
    Verifica se a sessao ainda e valida (menos de 1 hora)

    Input: tempo_sessao_str (str) - Timestamp da sessao
    Output: (bool) - True se valida, False caso contrario
    """
    if not tempo_sessao_str:
        return False

    try:
        session_timestamp = _parse_timestamp_to_unix(tempo_sessao_str)
        current_timestamp = int(datetime.now(timezone.utc).timestamp())

        diff_seconds = current_timestamp - session_timestamp

        is_valid = diff_seconds < 3600

        logger.info(f"[SESSION] Validacao de sessao: tempo_sessao={tempo_sessao_str}, diff_seconds={diff_seconds}, valida={is_valid}")

        return is_valid
    except Exception as e:
        logger.error(f"[SESSION] Erro ao validar sessao: {str(e)}")
        return False


def _get_previous_negotiation_data(telefone: str) -> dict:
    """
    Busca dados imutaveis da negociacao anterior para herdar na nova

    Input: telefone (str) - Telefone do motorista
    Output: (dict) - Dados imutaveis para herdar

    Campos herdados:
    - carga_id: ID da carga em negociacao
    - veiculo_cavalo: Objeto completo do veiculo cavalo
    - veiculo_cavalo_id: ID do veiculo cavalo
    - equipamento_ids: Lista de IDs de equipamentos
    - tipo_veiculo_id: ID do tipo de veiculo
    - tipo_equipamento_id: ID do tipo de equipamento
    - placa_cavalo: Placa do veiculo cavalo
    - placa_equipamento: Placa do equipamento
    - tempo_memoria: Timestamp da memoria (se ainda valido)
    """
    if not telefone:
        return {}

    try:
        from boto3.dynamodb.conditions import Key

        negociacao_table = dynamodb_resource.Table(NEGOCIACAO_TABLE)

        response = negociacao_table.query(
            KeyConditionExpression=Key('telefone').eq(telefone),
            ScanIndexForward=False,
            Limit=1
        )

        items = response.get('Items', [])
        if not items:
            logger.info(f"[DATA-INHERITANCE] Nenhuma negociacao anterior encontrada para {telefone}")
            return {}

        prev_item = items[0]
        inherited_data = {}

        immutable_fields = [
            'carga_id',
            'veiculo_cavalo',          # Objeto completo do veiculo cavalo
            'veiculo_cavalo_id',       # ID do veiculo cavalo
            'id_veiculo_equipamento',
            'equipamento_ids',         # Lista de IDs de equipamentos
            'tipo_veiculo_id',
            'tipo_equipamento_id',
            'tempo_memoria',
            'placa_cavalo',
            'placa_equipamento'
        ]

        for field in immutable_fields:
            if field in prev_item and prev_item[field]:
                inherited_data[field] = prev_item[field]

        if inherited_data:
            logger.info(f"[DATA-INHERITANCE] Herdando {len(inherited_data)} campos da negociacao anterior: {list(inherited_data.keys())}")
        else:
            logger.info(f"[DATA-INHERITANCE] Nenhum dado imutavel para herdar")

        return inherited_data

    except Exception as e:
        logger.error(f"[DATA-INHERITANCE] Erro ao buscar dados da negociacao anterior: {str(e)}")
        import traceback
        traceback.print_exc()
        return {}


def _save_session_timestamp_to_negociacao(telefone: str, tempo_sessao: str, session_id: str, negociacao_iniciada_em: str = None) -> bool:
    """
    Salva tempo_sessao, session_id e negociacao_iniciada_em na tabela negociacao

    Input: telefone (str), tempo_sessao (str), session_id (str), negociacao_iniciada_em (str opcional)
    Output: (bool) - True se salvou com sucesso, False caso contrario
    """
    if not telefone or not tempo_sessao or not session_id:
        logger.error("[SESSION] Parametros invalidos ao salvar tempo_sessao/session_id")
        return False

    try:
        negociacao_table = dynamodb_resource.Table(NEGOCIACAO_TABLE)

        inherited_data = _get_previous_negotiation_data(telefone)

        item = {
            'telefone': telefone,
            'tempo_sessao': tempo_sessao,
            'session_id': session_id
        }

        if negociacao_iniciada_em:
            item['negociacao_iniciada_em'] = negociacao_iniciada_em

        if inherited_data:
            if 'tempo_memoria' in inherited_data:
                tempo_memoria_inherited = str(inherited_data['tempo_memoria'])
                if _is_memory_valid(tempo_memoria_inherited):
                    logger.info(f"[DATA-INHERITANCE] tempo_memoria herdado ainda valido: {tempo_memoria_inherited}")
                else:
                    logger.info(f"[DATA-INHERITANCE] tempo_memoria herdado expirado, nao herdando")
                    del inherited_data['tempo_memoria']

            item.update(inherited_data)
            logger.info(f"[DATA-INHERITANCE] {len(inherited_data)} campos herdados incluidos na nova negociacao")

        negociacao_table.put_item(Item=item)

        logger.info(f"[SESSION] Salvo na negociacao para telefone {telefone}: tempo_sessao={tempo_sessao}, session_id={session_id}, negociacao_iniciada_em={negociacao_iniciada_em}")
        return True

    except Exception as e:
        logger.error(f"[SESSION] Erro ao salvar tempo_sessao/session_id na negociacao: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def _get_or_create_session_id(telefone: str) -> tuple:
    """
    Gerencia sessao do chatbot com separacao entre session_id e tempo_sessao

    Input: telefone (str) - Telefone do motorista
    Output: (str, bool) - Tupla com session_id e session_renewed (True quando session_id expirou e foi renovado)
    """
    if not telefone:
        logger.warning("[SESSION] Telefone vazio, usando apenas timestamp")
        now = datetime.now(timezone.utc)
        session_id = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return session_id, False

    logger.info(f"[SESSION] Gerenciando sessao para telefone: {telefone}")

    existing_tempo_sessao, existing_session_id = _get_session_timestamp_from_negociacao(telefone)

    now = datetime.now(timezone.utc)
    current_tempo_sessao = now.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    current_timestamp = int(now.timestamp())

    if not existing_tempo_sessao or not existing_session_id:
        logger.info("[SESSION] Criando nova sessao: nenhum registro existente")
        new_session_id = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        negociacao_iniciada_em = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        _save_session_timestamp_to_negociacao(telefone, current_tempo_sessao, new_session_id, negociacao_iniciada_em)
        logger.info(f"[SESSION] Novo session_id criado: {new_session_id}")
        return new_session_id, False

    session_id_timestamp = _parse_timestamp_to_unix(existing_session_id)
    session_age_seconds = current_timestamp - session_id_timestamp

    logger.info(f"[SESSION] session_id existente: {existing_session_id}, idade: {session_age_seconds}s, tempo_sessao: {existing_tempo_sessao}")

    if session_age_seconds < 3600:
        if existing_tempo_sessao == current_tempo_sessao:
            logger.info(f"[SESSION] Reutilizando session_id e tempo_sessao (mesma hora, idade: {session_age_seconds}s)")
            return existing_session_id, False

        else:
            logger.info(f"[SESSION] Reutilizando session_id, mas atualizando tempo_sessao (hora mudou, idade: {session_age_seconds}s)")
            logger.info(f"[SESSION] tempo_sessao antigo: {existing_tempo_sessao}, novo: {current_tempo_sessao}")
            negociacao_iniciada_em = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            _save_session_timestamp_to_negociacao(telefone, current_tempo_sessao, existing_session_id, negociacao_iniciada_em)
            return existing_session_id, False

    logger.info(f"[SESSION] Session_id expirado (idade: {session_age_seconds}s), criando novo")
    logger.info(f"[SESSION-RENEWAL] Session expired - will need to rebuild context from history")
    new_session_id = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    negociacao_iniciada_em = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    _save_session_timestamp_to_negociacao(telefone, current_tempo_sessao, new_session_id, negociacao_iniciada_em)
    logger.info(f"[SESSION] Novo session_id criado: {new_session_id}")
    return new_session_id, True

def _normalize_tempo_memoria_to_iso(tempo_memoria: str) -> str:
    """
    Normaliza tempo_memoria para formato ISO 8601 se estiver em formato compacto

    Input: tempo_memoria (str) - Timestamp em qualquer formato
    Output: (str) - Timestamp em formato ISO 8601 (YYYY-MM-DDTHH:MM:SS.ffffffZ)
    """
    if not tempo_memoria:
        return tempo_memoria

    tempo_str = str(tempo_memoria).strip()

    if 'T' in tempo_str and '-' in tempo_str:
        logger.info(f"[TEMPO-MEMORIA] Ja em formato ISO: {tempo_str}")
        return tempo_str

    if len(tempo_str) >= 14 and tempo_str.isdigit():
        try:
            year = int(tempo_str[0:4])
            month = int(tempo_str[4:6])
            day = int(tempo_str[6:8])
            hour = int(tempo_str[8:10])
            minute = int(tempo_str[10:12])
            second = int(tempo_str[12:14])
            microsecond = int(tempo_str[14:20]) if len(tempo_str) >= 20 else 0

            dt_obj = datetime(year, month, day, hour, minute, second, microsecond, tzinfo=timezone.utc)
            iso_str = dt_obj.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

            logger.info(f"[TEMPO-MEMORIA] Normalizado de compacto para ISO: {tempo_str} -> {iso_str}")
            return iso_str

        except Exception as e:
            logger.error(f"[TEMPO-MEMORIA] Erro ao normalizar formato compacto: {str(e)}")
            return tempo_str

    logger.warning(f"[TEMPO-MEMORIA] Formato desconhecido, mantendo: {tempo_str}")
    return tempo_str


def _get_memory_timestamp_from_negociacao(telefone: str) -> str:
    """
    Busca tempo_memoria mais recente da tabela negociacao

    Input: telefone (str) - Telefone do motorista
    Output: (str) - Timestamp do tempo_memoria ou None se nao encontrado
    """
    if not telefone:
        logger.error("[MEMORIA] Telefone vazio ao buscar tempo_memoria")
        return None

    try:
        from boto3.dynamodb.conditions import Key

        negociacao_table = dynamodb_resource.Table(NEGOCIACAO_TABLE)

        response = negociacao_table.query(
            KeyConditionExpression=Key('telefone').eq(telefone),
            ScanIndexForward=False,
            Limit=1,
            ProjectionExpression='tempo_memoria'
        )

        items = response.get('Items', [])

        if items and 'tempo_memoria' in items[0]:
            tempo_memoria_raw = str(items[0]['tempo_memoria'])

            tempo_memoria = _normalize_tempo_memoria_to_iso(tempo_memoria_raw)

            logger.info(f"[MEMORIA] tempo_memoria encontrado na negociacao para {telefone}: {tempo_memoria}")
            return tempo_memoria
        else:
            logger.info(f"[MEMORIA] Nenhum tempo_memoria encontrado na negociacao para {telefone}")
            return None

    except Exception as e:
        logger.error(f"[MEMORIA] Erro ao buscar tempo_memoria da negociacao: {str(e)}")
        import traceback
        traceback.print_exc()
        return None

def _is_memory_valid(tempo_memoria_str: str) -> bool:
    """
    Verifica se a memoria ainda e valida (menos de 7 dias)

    Input: tempo_memoria_str (str) - Timestamp da memoria
    Output: (bool) - True se valida, False caso contrario
    """
    if not tempo_memoria_str:
        return False

    try:
        memory_timestamp = _parse_timestamp_to_unix(tempo_memoria_str)
        current_timestamp = int(datetime.now(timezone.utc).timestamp())

        diff_seconds = current_timestamp - memory_timestamp

        MEMORY_TIMEOUT = 604800
        is_valid = diff_seconds < MEMORY_TIMEOUT

        logger.info(f"[MEMORIA] Validacao de memoria: tempo_memoria={tempo_memoria_str}, diff_seconds={diff_seconds}, valida={is_valid}")

        return is_valid
    except Exception as e:
        logger.error(f"[MEMORIA] Erro ao validar memoria: {str(e)}")
        return False

def _save_memory_timestamp_to_negociacao(telefone: str, tempo_memoria: str) -> bool:
    """
    Salva tempo_memoria na tabela negociacao

    Input: telefone (str), tempo_memoria (str)
    Output: (bool) - True se salvou com sucesso, False caso contrario
    """
    if not telefone or not tempo_memoria:
        logger.error("[MEMORIA] Parametros invalidos ao salvar tempo_memoria")
        return False

    try:
        negociacao_table = dynamodb_resource.Table(NEGOCIACAO_TABLE)

        tempo_sessao_recente, session_id_recente = _get_session_timestamp_from_negociacao(telefone)

        if not tempo_sessao_recente or not session_id_recente:
            logger.warning("[MEMORIA] Nenhum tempo_sessao encontrado, criando nova sessao para salvar memoria")
            now = datetime.now(timezone.utc)
            tempo_sessao_recente = now.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
            session_id_recente = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            negociacao_iniciada_em = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            negociacao_table.put_item(
                Item={
                    'telefone': telefone,
                    'tempo_sessao': tempo_sessao_recente,
                    'session_id': session_id_recente,
                    'tempo_memoria': tempo_memoria,
                    'negociacao_iniciada_em': negociacao_iniciada_em
                }
            )
        else:
            negociacao_table.update_item(
                Key={
                    'telefone': telefone,
                    'tempo_sessao': tempo_sessao_recente
                },
                UpdateExpression='SET tempo_memoria = :tm',
                ExpressionAttributeValues={
                    ':tm': tempo_memoria
                }
            )

        logger.info(f"[MEMORIA] tempo_memoria salvo na negociacao: {tempo_memoria} para telefone {telefone} (tempo_sessao: {tempo_sessao_recente})")
        return True

    except Exception as e:
        logger.error(f"[MEMORIA] Erro ao salvar tempo_memoria na negociacao: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def _get_or_create_memory_id(telefone: str) -> str:
    """
    Gerencia memoria do chatbot usando tabela negociacao

    Input: telefone (str) - Telefone do motorista
    Output: (str) - Memory ID no formato {telefone}_mem_{tempo_memoria}
    """
    if not telefone:
        logger.warning("[MEMORIA] Telefone vazio, usando apenas timestamp para memoria")
        now = datetime.now(timezone.utc)
        tempo_memoria = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return f"mem_{tempo_memoria}"

    logger.info(f"[MEMORIA] Gerenciando memoria para telefone: {telefone} (usando tabela negociacao)")

    existing_tempo_memoria = _get_memory_timestamp_from_negociacao(telefone)

    if not existing_tempo_memoria:
        logger.info("[MEMORIA] Criando nova memoria: nenhum tempo_memoria existente na negociacao")
        now = datetime.now(timezone.utc)
        tempo_memoria = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        _save_memory_timestamp_to_negociacao(telefone, tempo_memoria)
        memory_id = f"{telefone}_mem_{tempo_memoria}"
        logger.info(f"[MEMORIA] Novo memory_id criado: {memory_id}")
        return memory_id

    if _is_memory_valid(existing_tempo_memoria):
        memory_id = f"{telefone}_mem_{existing_tempo_memoria}"
        current_timestamp = int(datetime.now(timezone.utc).timestamp())
        memory_timestamp = _parse_timestamp_to_unix(existing_tempo_memoria)
        age_seconds = current_timestamp - memory_timestamp
        age_days = age_seconds / 86400
        logger.info(f"[MEMORIA] Reutilizando memoria existente: {memory_id} (idade: {age_days:.1f} dias)")
        return memory_id

    current_timestamp = int(datetime.now(timezone.utc).timestamp())
    memory_timestamp = _parse_timestamp_to_unix(existing_tempo_memoria)
    age_seconds = current_timestamp - memory_timestamp
    age_days = age_seconds / 86400
    logger.info(f"[MEMORIA] Memoria expirada (idade: {age_days:.1f} dias), criando nova")
    now = datetime.now(timezone.utc)
    tempo_memoria = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    _save_memory_timestamp_to_negociacao(telefone, tempo_memoria)
    memory_id = f"{telefone}_mem_{tempo_memoria}"
    logger.info(f"[MEMORIA] Novo memory_id criado: {memory_id}")
    return memory_id

def _get_session_attributes_from_db(telefone: str) -> dict:
    """
    Busca dados do motorista no DynamoDB para construir session attributes

    Input: telefone (str) - Telefone do motorista
    Output: (dict) - Session attributes com campos permitidos (sem dados sensiveis)
    """
    if not telefone:
        logger.warning("[SESSION-ATTRS] Telefone vazio, retornando session attributes vazio")
        return {}

    try:
        response = dynamodb.query(
            TableName=TABLE_NAME,
            IndexName="telefone-index",
            KeyConditionExpression="telefone = :telefone",
            ExpressionAttributeValues={":telefone": {"S": telefone}},
            ScanIndexForward=False,
            Limit=1,
        )

        items = response.get("Items", [])
        if not items:
            logger.info(f"[SESSION-ATTRS] Nenhum motorista encontrado para telefone {telefone}")
            return {"telefone": telefone}

        item = items[0]

        now_utc = datetime.now(timezone.utc)
        session_attrs = {
            "telefone": telefone,
            "id_motorista": item.get("id_motorista", {}).get("S", ""),
            "nome": item.get("nome", {}).get("S", ""),
            "cadastrado_telefone": item.get("cadastrado_telefone", {}).get("S", "false"),
            "cadastrado_cpf": item.get("cadastrado_cpf", {}).get("S", "false"),
            "timestamp": str(int(now_utc.timestamp())),
            "data_atual": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
        }

        session_attrs = {k: v for k, v in session_attrs.items() if v}

        logger.info(f"[SESSION-ATTRS] Session attributes construidos para {telefone}: {list(session_attrs.keys())}")

        return session_attrs

    except Exception as e:
        logger.error(f"[SESSION-ATTRS] Erro ao buscar session attributes: {str(e)}")
        return {"telefone": telefone}

def _parse_data_hora_envio(data_hora_str: str) -> Optional[datetime]:
    """
    Faz parse de dataHoraEnvio do formato DD/MM/YYYY HH:MM:SS.mmm para datetime

    Input: data_hora_str (str) - String no formato "10/12/2025 11:16:08.364"
    Output: (datetime) - Objeto datetime UTC ou None se parse falhar
    """
    if not data_hora_str:
        return None

    try:
        # Formato: DD/MM/YYYY HH:MM:SS.mmm
        # Remover milissegundos se presentes para simplificar parse
        if '.' in data_hora_str:
            data_hora_base, millis = data_hora_str.rsplit('.', 1)
            # Parse base (sem milissegundos)
            dt_obj = datetime.strptime(data_hora_base, "%d/%m/%Y %H:%M:%S")
            # Adicionar milissegundos
            microseconds = int(millis) * 1000  # Converter milissegundos para microsegundos
            dt_obj = dt_obj.replace(microsecond=microseconds)
        else:
            # Sem milissegundos
            dt_obj = datetime.strptime(data_hora_str, "%d/%m/%Y %H:%M:%S")

        # Assumir UTC (mensagens do sistema são em UTC)
        dt_obj = dt_obj.replace(tzinfo=timezone.utc)

        logger.info(f"[OFFER-CONTEXT] Data/hora parsed: {data_hora_str} -> {dt_obj.isoformat()}")
        return dt_obj

    except Exception as e:
        logger.error(f"[OFFER-CONTEXT] Erro ao fazer parse de data_hora_envio '{data_hora_str}': {str(e)}")
        return None

def _buscar_negociacoes_por_hora(telefone: str, hora_timestamp: str) -> list:
    """
    Busca negociacoes para uma hora especifica

    Input: telefone (str), hora_timestamp (str) - Timestamp com precisao de hora (YYYY-MM-DDTHH:00:00Z)
    Output: (list) - Lista de items da negociacao (pode estar vazia)
    """
    try:
        from boto3.dynamodb.conditions import Key

        negociacao_table = dynamodb_resource.Table(NEGOCIACAO_TABLE)

        response = negociacao_table.query(
            KeyConditionExpression=Key('telefone').eq(telefone) & Key('tempo_sessao').eq(hora_timestamp),
            ProjectionExpression='carga_id, tempo_sessao, negociacao_iniciada_em'
        )

        items = response.get('Items', [])
        logger.info(f"[OFFER-CONTEXT] Encontrados {len(items)} items para hora {hora_timestamp}")
        return items

    except Exception as e:
        logger.error(f"[OFFER-CONTEXT] Erro ao buscar negociações por hora: {str(e)}")
        return []

def _buscar_carga_id_da_negociacao(telefone: str, data_hora_mensagem: str = None) -> Optional[int]:
    """
    Busca carga_id da tabela negociacao com matching preciso

    Input: telefone (str), data_hora_mensagem (str opcional) - dataHoraEnvio da mensagem (formato DD/MM/YYYY HH:MM:SS.mmm)
    Output: (int) - carga_id se encontrado, None caso contrario
    """
    if not telefone:
        logger.warning("[OFFER-CONTEXT] Telefone vazio ao buscar carga_id")
        return None

    try:
        from boto3.dynamodb.conditions import Key

        # ===== MATCHING PRECISO COM dataHoraEnvio =====
        if data_hora_mensagem:
            logger.info(f"[OFFER-CONTEXT] Matching preciso com dataHoraEnvio: {data_hora_mensagem}")

            # Parse dataHoraEnvio
            dt_mensagem = _parse_data_hora_envio(data_hora_mensagem)

            if dt_mensagem:
                # Gerar timestamps com precisão de hora
                # Hora da mensagem
                hora_mensagem = dt_mensagem.replace(minute=0, second=0, microsecond=0)
                hora_mensagem_ts = hora_mensagem.strftime("%Y-%m-%dT%H:%M:%SZ")

                # Hora anterior (pode ter começado na hora anterior)
                from datetime import timedelta
                hora_anterior = hora_mensagem - timedelta(hours=1)
                hora_anterior_ts = hora_anterior.strftime("%Y-%m-%dT%H:%M:%SZ")

                logger.info(f"[OFFER-CONTEXT] Buscando em hora_mensagem: {hora_mensagem_ts} e hora_anterior: {hora_anterior_ts}")

                # Buscar ambas as horas
                items_hora_mensagem = _buscar_negociacoes_por_hora(telefone, hora_mensagem_ts)
                items_hora_anterior = _buscar_negociacoes_por_hora(telefone, hora_anterior_ts)

                # Combinar resultados
                all_items = items_hora_mensagem + items_hora_anterior

                if all_items:
                    logger.info(f"[OFFER-CONTEXT] Total de {len(all_items)} negociações encontradas")

                    # Se só tem 1, usar essa
                    if len(all_items) == 1:
                        item = all_items[0]
                        logger.info(f"[OFFER-CONTEXT] Única negociação encontrada, usando essa")
                    else:
                        # Múltiplas negociações - fazer matching por timestamp preciso
                        logger.info(f"[OFFER-CONTEXT] Múltiplas negociações - fazendo matching preciso")

                        # Converter dt_mensagem para timestamp ISO
                        mensagem_ts = dt_mensagem.isoformat()

                        # Encontrar negociação mais próxima ANTES da mensagem
                        best_match = None
                        best_diff = None

                        for item in all_items:
                            negociacao_iniciada_em = item.get('negociacao_iniciada_em')

                            if negociacao_iniciada_em:
                                # Comparar timestamps
                                # negociacao_iniciada_em deve ser ANTES ou IGUAL a mensagem_ts
                                if negociacao_iniciada_em <= mensagem_ts:
                                    diff = mensagem_ts - negociacao_iniciada_em

                                    if best_diff is None or diff < best_diff:
                                        best_diff = diff
                                        best_match = item
                                        logger.info(f"[OFFER-CONTEXT] Melhor match atual: negociacao_iniciada_em={negociacao_iniciada_em}, diff={diff}")

                        if best_match:
                            item = best_match
                            logger.info(f"[OFFER-CONTEXT] Match encontrado por timestamp preciso")
                        else:
                            # Fallback: usar mais recente
                            item = all_items[0]
                            logger.warning(f"[OFFER-CONTEXT] Nenhum match preciso, usando mais recente")

                    # Extrair carga_id
                    if 'carga_id' in item:
                        carga_id = item['carga_id']
                        logger.info(f"[OFFER-CONTEXT] carga_id encontrado: {carga_id}")
                        if isinstance(carga_id, Decimal):
                            return int(carga_id)
                        return int(carga_id)

        # ===== FALLBACK: BUSCAR MAIS RECENTE =====
        logger.info(f"[OFFER-CONTEXT] Fallback: buscando negociação mais recente")

        negociacao_table = dynamodb_resource.Table(NEGOCIACAO_TABLE)

        # Query usando partition key para pegar o registro mais recente
        response = negociacao_table.query(
            KeyConditionExpression=Key('telefone').eq(telefone),
            ScanIndexForward=False,  # Ordenar descendente (mais recente primeiro)
            Limit=1,  # Pegar apenas o mais recente
            ProjectionExpression='carga_id'
        )

        items = response.get('Items', [])

        if items and 'carga_id' in items[0]:
            carga_id = items[0]['carga_id']
            logger.info(f"[OFFER-CONTEXT] carga_id encontrado (fallback): {carga_id}")
            if isinstance(carga_id, Decimal):
                return int(carga_id)
            return int(carga_id)
        else:
            logger.info(f"[OFFER-CONTEXT] Nenhum carga_id encontrado para telefone {telefone}")
            return None

    except Exception as e:
        logger.error(f"[OFFER-CONTEXT] Erro ao buscar carga_id: {str(e)}")
        import traceback
        traceback.print_exc()
        return None

def _buscar_dados_oferta_por_carga_id(carga_id: int) -> Optional[dict]:
    """
    Busca dados da oferta via API usando carga_id

    Input: carga_id (int) - ID da carga
    Output: (dict) - Dados da oferta se encontrado, None caso contrario
    """
    if not carga_id:
        logger.warning("[OFFER-CONTEXT] carga_id vazio ao buscar oferta")
        return None

    try:
        # Get auth token
        sucesso, token, erro = obter_token_do_parameter_store()
        if not sucesso:
            logger.error(f"[OFFER-CONTEXT] Erro ao obter token: {erro}")
            return None

        # Build API request
        api_url = os.environ.get('API_BASE_URL', 'https://api-staging.rodosafra.net/api')
        endpoint = f"{api_url}/publico/carga/{carga_id}"

        headers = {
            'Content-Type': 'application/json',
            'Cookie': token
        }

        logger.info(f"[OFFER-CONTEXT] Buscando oferta via API: {endpoint}")

        # Make API call with retry logic
        response = retry_on_timeout(
            lambda: http.request(
                'GET',
                endpoint,
                headers=headers,
                timeout=10.0
            ),
            max_retries=3,
            operation_name="Fetch offer data"
        )

        if response.status == 200:
            oferta_data = json.loads(response.data.decode('utf-8'))
            logger.info(f"[OFFER-CONTEXT] Oferta encontrada: ID {carga_id}")
            return oferta_data
        else:
            logger.warning(f"[OFFER-CONTEXT] Oferta não encontrada: status {response.status}")
            return None

    except Exception as e:
        logger.error(f"[OFFER-CONTEXT] Erro ao buscar oferta: {str(e)}")
        import traceback
        traceback.print_exc()
        return None

def _construir_contexto_oferta(oferta_data: dict) -> dict:
    """
    Constrói contexto da oferta para adicionar aos session_attributes

    Input: oferta_data (dict) - Dados da oferta da API
    Output: (dict) - Contexto da oferta formatado
    """
    if not oferta_data:
        return {}

    try:
        contexto = {}

        # Basic offer info
        contexto['carga_id'] = str(oferta_data.get('id', ''))
        contexto['material'] = oferta_data.get('material', '')
        contexto['carga_urgente'] = str(oferta_data.get('carga_urgente', False))

        # Origin
        origem = oferta_data.get('origem', {})
        endereco_origem = origem.get('endereco', {})
        contexto['origem_cidade'] = endereco_origem.get('cidade', '')
        contexto['origem_estado'] = endereco_origem.get('estado', '')

        # Destination
        destino = oferta_data.get('destino', {})
        endereco_destino = destino.get('endereco', {})
        contexto['destino_cidade'] = endereco_destino.get('cidade', '')
        contexto['destino_estado'] = endereco_destino.get('estado', '')

        # Freight value
        frete_valor = oferta_data.get('frete_motorista') or oferta_data.get('valor_frete', 0)
        try:
            frete_valor_num = float(frete_valor) if frete_valor else 0.0
            frete_formatado = f"R$ {frete_valor_num:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
        except:
            frete_formatado = "R$ 0,00"

        contexto['valor_frete'] = frete_formatado

        # Payment type
        tipo_pagamento = oferta_data.get('tipo_pagamento_frete', 'fixo')
        contexto['tipo_pagamento_frete'] = tipo_pagamento
        if tipo_pagamento == 'tonelada':
            contexto['valor_frete_descricao'] = f"{frete_formatado} por tonelada"
        else:
            contexto['valor_frete_descricao'] = f"{frete_formatado} (valor fixo)"

        # Period
        contexto['inicio_periodo'] = oferta_data.get('inicio_periodo', '')
        contexto['fim_periodo'] = oferta_data.get('fim_periodo', '')

        # Vehicle requirements
        veiculo = oferta_data.get('veiculo', {})
        tipos_veiculo = veiculo.get('tipos', [])
        if tipos_veiculo:
            contexto['tipos_veiculo'] = ', '.join(tipos_veiculo)

        equipamentos = veiculo.get('equipamentos', [])
        if equipamentos:
            contexto['equipamentos_necessarios'] = ', '.join(equipamentos)

        # Build summary text
        summary_parts = []
        summary_parts.append(
            f"Oferta de transporte de {contexto.get('material', 'carga')} "
            f"de {contexto.get('origem_cidade', '')} - {contexto.get('origem_estado', '')} "
            f"para {contexto.get('destino_cidade', '')} - {contexto.get('destino_estado', '')}"
        )
        summary_parts.append(f"Valor do frete: {contexto.get('valor_frete_descricao', '')}")

        if contexto.get('carga_urgente') == 'True':
            summary_parts.append("CARGA URGENTE")

        if contexto.get('tipos_veiculo'):
            summary_parts.append(f"Tipos de veículo: {contexto['tipos_veiculo']}")

        if contexto.get('equipamentos_necessarios'):
            summary_parts.append(f"Equipamentos necessários: {contexto['equipamentos_necessarios']}")

        contexto['oferta_resumo'] = '. '.join(summary_parts) + '.'

        logger.info(f"[OFFER-CONTEXT] Contexto construído para carga_id {contexto.get('carga_id')}")

        return contexto

    except Exception as e:
        logger.error(f"[OFFER-CONTEXT] Erro ao construir contexto: {str(e)}")
        import traceback
        traceback.print_exc()
        return {}

def armazenar_mensagem_pendente(cod_conversa: str, mensagem: Dict[str, Any]) -> Dict[str, Any]:
    """
    Armazena uma mensagem pendente no DynamoDB e gerencia o timer de acumulacao

    Input: cod_conversa (str), mensagem (Dict[str, Any])
    Output: (Dict[str, Any]) - Dicionario com deve_processar, is_first_message e outras informacoes
    """
    try:
        timestamp_atual = Decimal(str(datetime.now(timezone.utc).timestamp()))
        
        sessoes_table = dynamodb_resource.Table(SESSOES_TABLE_NAME)
        
        # Tenta obter a sessao existente
        response = sessoes_table.get_item(
            Key={
                'telefone': cod_conversa,
                'session_id': cod_conversa
            }
        )
        
        item = response.get('Item')
        
        # Verifica se existe um timer ativo
        if item and item.get('timer_active'):
            pending_messages = item.get('pending_messages', [])
            
            # Timer ativo - adiciona mensagem e RESETA timer para MESSAGE_EXTENSION_DELAY
            pending_messages.append({
                'texto': mensagem.get('texto', ''),
                'tipo': mensagem.get('tipo', 'TEXT'),
                'timestamp': timestamp_atual,
                'codMensagem': mensagem.get('codMensagem', ''),
                'nomeArquivo': mensagem.get('nomeArquivo', ''),
                'localizacao': mensagem.get('localizacao', ''),
                'mensagemResposta': mensagem.get('mensagemResposta', '')
            })

            logger.info(pending_messages)
            
            # IMPORTANTE: Atualiza timer_timestamp para invalidar timer anterior
            # Isso efetivamente "cancela" o processamento agendado anterior
            sessoes_table.update_item(
                Key={
                    'telefone': cod_conversa,
                    'session_id': cod_conversa
                },
                UpdateExpression='SET pending_messages = :msgs, last_message_timestamp = :ts, last_message_text = :txt, timer_timestamp = :new_timer',
                ExpressionAttributeValues={
                    ':msgs': pending_messages,
                    ':ts': timestamp_atual,
                    ':txt': mensagem.get('texto', ''),
                    ':new_timer': timestamp_atual  # Novo timer timestamp
                }
            )
            
            logger.info(f"[ACCUMULATE] Mensagem adicionada ao lote. Total: {len(pending_messages)}. Timer resetado para {MESSAGE_EXTENSION_DELAY}s")
            
            agendar_processamento(cod_conversa, timestamp_atual, MESSAGE_EXTENSION_DELAY)
            
            return {
                'deve_processar': False,
                'is_first_message': False,
                'mensagens_acumuladas': len(pending_messages),
                'timer_resetado': True
            }
        
        pending_messages = [{
            'texto': mensagem.get('texto', ''),
            'tipo': mensagem.get('tipo', 'TEXT'),
            'timestamp': timestamp_atual,
            'codMensagem': mensagem.get('codMensagem', ''),
            'nomeArquivo': mensagem.get('nomeArquivo', ''),
            'localizacao': mensagem.get('localizacao', ''),
            'mensagemResposta': mensagem.get('mensagemResposta', '')

        }]
        
        logger.info(f"[ACCUMULATE] Nova sessao de acumulacao iniciada: {pending_messages}")

        sessoes_table.put_item(
            Item={
                'telefone': cod_conversa,
                'session_id': cod_conversa,
                'timer_timestamp': timestamp_atual,
                'pending_messages': pending_messages,
                'last_message_timestamp': timestamp_atual,
                'last_message_text': mensagem.get('texto', ''),
                'timer_active': True
            }
        )
        
        logger.info(f"[ACCUMULATE] Nova sessao de acumulacao iniciada. Timer inicial de {MESSAGE_ACCUMULATION_DELAY}s ativado")
        
        # Agenda o processamento apos MESSAGE_ACCUMULATION_DELAY segundos (5s)
        agendar_processamento(cod_conversa, timestamp_atual, MESSAGE_ACCUMULATION_DELAY)
        
        return {
            'deve_processar': False,
            'is_first_message': True,
            'mensagens_acumuladas': 1
        }
        
    except Exception as e:
        logger.error(f"[ACCUMULATE] Erro ao armazenar mensagem pendente: {str(e)}")
        return {
            'deve_processar': True,
            'is_first_message': True,
            'erro': str(e)
        }

def agendar_processamento(cod_conversa: str, timer_timestamp: Decimal, delay_seconds: int):
    """
    Agenda o processamento das mensagens acumuladas apos delay_seconds usando invocacao assincrona da propria Lambda

    Input: cod_conversa (str), timer_timestamp (Decimal), delay_seconds (int)
    Output: Nenhum
    """
    try:
        payload = {
            'acao': 'processar_mensagens_acumuladas',
            'codConversa': cod_conversa,
            'timer_timestamp': float(timer_timestamp),
            'delay_seconds': delay_seconds
        }
        
        response = lambda_client.invoke(
            FunctionName=os.environ.get('AWS_LAMBDA_FUNCTION_NAME'),
            InvocationType='Event',
            Payload=json.dumps(payload)
        )
        
        logger.info(f"[SCHEDULE] Processamento agendado para {delay_seconds}s. Response: {response['StatusCode']}")
        
    except Exception as e:
        logger.error(f"[SCHEDULE] Erro ao agendar processamento: {str(e)}")

def obter_e_limpar_mensagens_acumuladas(cod_conversa: str, timer_timestamp: float) -> list:
    """
    Obtem todas as mensagens acumuladas e limpa a sessao

    Input: cod_conversa (str), timer_timestamp (float)
    Output: (list) - Lista de mensagens acumuladas ou lista vazia se timer invalido
    """
    try:
        sessoes_table = dynamodb_resource.Table(SESSOES_TABLE_NAME)
        
        response = sessoes_table.get_item(
            Key={
                'telefone': cod_conversa,
                'session_id': cod_conversa
            }
        )
        
        item = response.get('Item')
        
        if not item:
            logger.info("[ACCUMULATE] Nenhuma sessao encontrada para processar")
            return []
        
        stored_timer = float(item.get('timer_timestamp', 0))
        if abs(stored_timer - timer_timestamp) > 0.1:
            logger.info(f"[ACCUMULATE] Timer invalidado por nova mensagem. Stored: {stored_timer}, Expected: {timer_timestamp}. Abortando processamento.")
            return []
        
        pending_messages = item.get('pending_messages', [])
        
        if not pending_messages:
            logger.info("[ACCUMULATE] Nenhuma mensagem pendente para processar")
            return []
        
        sessoes_table.update_item(
            Key={
                'telefone': cod_conversa,
                'session_id': cod_conversa
            },
            UpdateExpression='SET pending_messages = :empty, timer_active = :false REMOVE timer_timestamp',
            ExpressionAttributeValues={
                ':empty': [],
                ':false': False
            }
        )
        
        logger.info(f"[ACCUMULATE] Obtidas {len(pending_messages)} mensagens para processar")
        return pending_messages
        
    except Exception as e:
        logger.error(f"[ACCUMULATE] Erro ao obter mensagens acumuladas: {str(e)}")
        return []

def combinar_mensagens(mensagens: list) -> str:
    """
    Combina multiplas mensagens em um unico texto para enviar ao chatbot

    Input: mensagens (list) - Lista de mensagens
    Output: (str) - Texto combinado
    """
    if not mensagens:
        return ""

    if len(mensagens) == 1:
        return mensagens[0].get('texto', '')
    
    textos = [msg.get('texto', '') for msg in mensagens if msg.get('texto')]
    texto_combinado = '\n'.join(textos)
 
    logger.info(f"[COMBINE] Mensagens combinadas: {len(mensagens)} mensagens em um texto")
    return texto_combinado

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
            Name=PARAMETER_STORE_TOKEN_NAME, WithDecryption=True
        )

        token = response["Parameter"]["Value"]

        if not token:
            return False, None, "Token vazio no Parameter Store"

        auth_cookie_cache = token
        logger.info("[TOKEN] Token obtido com sucesso do Parameter Store")

        return True, token, None

    except ClientError as e:
        error_code = e.response["Error"]["Code"]

        if error_code == "ParameterNotFound":
            logger.error(f"[TOKEN] Token nao encontrado no Parameter Store: {PARAMETER_STORE_TOKEN_NAME}")
            return False, None, "Token nao encontrado no Parameter Store"

        elif error_code == "AccessDeniedException":
            logger.error("[TOKEN] Sem permissao para acessar Parameter Store")
            return False, None, "Sem permissao para acessar token"

        else:
            logger.error(f"[TOKEN] Erro ao acessar Parameter Store: {error_code}")
            return False, None, f"Erro ao obter token: {error_code}"

    except Exception as e:
        logger.error(f"[TOKEN] Erro inesperado ao obter token: {str(e)}")
        return False, None, f"Erro inesperado: {str(e)}"

def processar_mensagens_acumuladas_handler(event, context):
    """
    Handler para processar mensagens acumuladas apos o timer

    Input: event (dict), context (object)
    Output: (dict) - Resposta HTTP com resultado do processamento
    """
    try:
        cod_conversa = event.get('codConversa')
        timer_timestamp = event.get('timer_timestamp')
        delay_seconds = event.get('delay_seconds', MESSAGE_ACCUMULATION_DELAY)
        
        if not cod_conversa or not timer_timestamp:
            logger.error("[HANDLER] Dados insuficientes para processar mensagens acumuladas")
            return {
                "statusCode": 400,
                "body": json.dumps({"success": False, "message": "Dados insuficientes"})
            }
        
        logger.info(f"[HANDLER] Aguardando {delay_seconds}s antes de processar mensagens de {cod_conversa}")
        
        time.sleep(delay_seconds)
        
        logger.info(f"[HANDLER] Timer expirado. Processando mensagens acumuladas para {cod_conversa}")
        
        mensagens = obter_e_limpar_mensagens_acumuladas(cod_conversa, timer_timestamp)
        
        if not mensagens:
            logger.info("[HANDLER] Nenhuma mensagem para processar (timer pode ter sido resetado)")
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "success": True,
                    "processado": False,
                    "motivo": "Timer invalidado ou nenhuma mensagem pendente"
                })
            }

        texto_combinado = combinar_mensagens(mensagens)
        
        answer_context_message = [msg for msg in mensagens if msg.get('mensagemResposta')]
        if answer_context_message:
            for answer_msg in answer_context_message:
                texto = answer_msg["mensagemResposta"].get("texto")
                if texto:
                    texto_f = f"Todos os textos inclusos estão respondendo essa mensagem, ou utilizando-a como contexto: {texto}. \n Resposta do motorista:"
                    texto_combinado = texto_f + '\n' + texto_combinado
                else:
                    if answer_msg["mensagemResposta"]["tipo"] == 'audio':
                        texto_f = f"Todos os textos inclusos estão respondendo essa mensagem, ou utilizando-a como contexto. Ela é um audio. A transcrição dele está marcada como Texto Transcrito. \nResposta do motorista:"
                        texto_combinado = texto_f + '\n' + texto_combinado
                    elif answer_msg["mensagemResposta"]["tipo"] == 'document':
                        texto_f = f"Todos os textos inclusos estão respondendo essa mensagem, ou utilizando-a como contexto. Ela é um documento. As informações sobre ele estãrao marcadas como Elementos inferidos pelo OCR do documento. \nResposta do motorista:"
                        texto_combinado = texto_f + '\n' + texto_combinado
                    elif answer_msg["mensagemResposta"]["tipo"] == 'image':
                        texto_f = f"Todos os textos inclusos estão respondendo essa mensagem, ou utilizando-a como contexto. Ela é uma imagem. As informações sobre ela estãrao marcadas como Elementos inferidos pelo OCR da imagem. \nResposta do motorista:"
                        texto_combinado = texto_f + '\n' + texto_combinado
                    elif answer_msg["mensagemResposta"]["tipo"] == 'location':  
                        texto_f = f"Todos os textos inclusos estão respondendo essa mensagem, ou utilizando-a como contexto. Ela é uma localização. As informações sobre ela estãrao marcadas como Motorista enviou a seguinte localização. \nResposta do motorista:"
                        texto_combinado = texto_f + '\n' + texto_combinado     

        audio_messages = [msg for msg in mensagens if msg.get('tipo') == 'audio' and msg.get('nomeArquivo')]
        if audio_messages:
            logger.info(f"[HANDLER] Encontrados {len(audio_messages)} audios para transcrever")
            textos_transcritos = []
            for audio_msg in audio_messages:
                texto_transcrito = transcrever_audio(audio_msg)
                if texto_transcrito:
                    textos_transcritos.append(texto_transcrito)
            
            if textos_transcritos:
                texto_combinado = '\n'.join(textos_transcritos) + '\n' + texto_combinado

        location_messages = [msg for msg in mensagens if msg.get('tipo') == 'location']
        if location_messages:
            logger.info(f"[HANDLER] Encontrados {len(location_messages)} mensagens de localizacao")
            texto_location = []
            for location_msg in location_messages:
                texto = tratar_localizacao(location_msg)
                if texto:
                    texto_location.append(texto)
                    
            if texto_location:
                texto_combinado = '\n'.join(texto_location) + '\n' + texto_combinado    
        
        document_messages = [msg for msg in mensagens if msg.get('tipo') == 'document' and msg.get('nomeArquivo')]
        if document_messages:
            logger.info(f"[HANDLER] Encontrados {len(document_messages)} documentos para ler")
            documentos_lidos = []
            for doc_msg in document_messages:
                texto = tratar_documento(doc_msg)
                if texto:
                    documentos_lidos.append(texto)
            
            if documentos_lidos:
                texto_combinado = '\n'.join(documentos_lidos) + '\n' + texto_combinado

        image_messages = [msg for msg in mensagens if msg.get('tipo') == 'image' and msg.get('nomeArquivo')]
        if image_messages:
            logger.info(f"[HANDLER] Encontrados {len(image_messages)} imagens para ler")
            imagens_vistas = []
            for img_msg in image_messages:
                texto = tratar_imagem(img_msg)
                if texto:
                    imagens_vistas.append(texto)
            
            if imagens_vistas:
                texto_combinado = '\n'.join(imagens_vistas) + '\n' + texto_combinado        

        # Cria mensagem combinada para processar
        mensagem_combinada = {
            'texto': texto_combinado,
            'tipo': 'TEXT',
            'codMensagem': f"combined_{len(mensagens)}msgs",
            'remetente': mensagens[0].get('remetente', 'Motorista') if mensagens else 'Motorista'
        }
        logger.info(f"Mensagem combinada: {mensagem_combinada}")

        try:
            logger.info(f"[MESSAGE-HISTORY] Salvando mensagem combinada do motorista para {cod_conversa}")
            save_driver_message(
                telefone=cod_conversa,
                mensagem=mensagem_combinada,
                table_name=NEGOCIACAO_TABLE
            )
            logger.info(f"[MESSAGE-HISTORY] Mensagem do motorista salva no historico")
        except Exception as hist_error:
            logger.error(f"[MESSAGE-HISTORY] Erro ao salvar mensagem do motorista: {str(hist_error)}")

        return processar_e_responder(cod_conversa, mensagem_combinada)
        
    except Exception as e:
        logger.error(f"[HANDLER] Erro ao processar mensagens acumuladas: {str(e)}")
        import traceback
        traceback.print_exc()
        
        return {
            "statusCode": 500,
            "body": json.dumps({"success": False, "error": str(e)})
        }

def buscar_mensagens_nao_lidas(cod_conversa: str) -> list:
    """
    Busca mensagens nao lidas da conversa via Chat API

    Input: cod_conversa (str) - Codigo da conversa
    Output: (list) - Lista de mensagens nao lidas do motorista
    """
    try:
        # Obtem token de autenticacao
        if not auth_cookie_cache:
            sucesso, token, erro = obter_token_do_parameter_store()
            if not sucesso:
                logger.error(f"[API] Erro ao obter token: {erro}")
                return []
        else:
            token = auth_cookie_cache

        chat_api_base_url = "https://api-gateway.rodosafra.net:8760"
        url = f"{chat_api_base_url}/api/chat/mensagens"

        logger.info(f"[API] Buscando mensagens nao lidas para {cod_conversa}")

        response = http.request(
            'GET',
            url,
            fields={
                'codConversa': cod_conversa,
                'pagina': '0',
                'tamanho': '50'
            },
            headers={
                'Cookie': token,
                'Accept': 'application/json'
            },
            timeout=10.0
        )

        if response.status != 200:
            logger.error(f"[API] Erro ao buscar mensagens: status {response.status}")
            return []

        data = json.loads(response.data.decode('utf-8'))
        messages = data.get('content', [])

        # Coleta mensagens do motorista (isMensagemContato=true) ate encontrar primeira mensagem nossa
        driver_messages = []
        for msg in messages:
            if msg.get('isMensagemContato') == True:
                driver_messages.append(msg)
            else:
                # Para quando encontrar primeira mensagem nossa (chatbot/atendente)
                break

        logger.info(f"[API] Mensagens nao lidas encontradas: {len(driver_messages)}")
        return driver_messages

    except Exception as e:
        logger.error(f"[API] Erro ao buscar mensagens nao lidas: {str(e)}")
        import traceback
        traceback.print_exc()
        return []

def handle_sse_to_websocket_transition(cod_conversa: str) -> Dict[str, Any]:
    """
    Processa transicao de SSE para WebSocket - busca mensagens nao lidas e processa com contexto

    Input: cod_conversa (str) - Codigo da conversa
    Output: (Dict[str, Any]) - Resposta HTTP com resultado do processamento
    """
    try:
        logger.info(f"[SSE->WS] Iniciando transicao SSE para WebSocket: {cod_conversa}")

        # Busca mensagens nao lidas
        unread_messages = buscar_mensagens_nao_lidas(cod_conversa)

        if not unread_messages:
            logger.info(f"[SSE->WS] Nenhuma mensagem nao lida encontrada para {cod_conversa}")
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "success": True,
                    "processado": True,
                    "respondido": False,
                    "motivo": "Nenhuma mensagem nao lida encontrada",
                    "mensagens_processadas": 0
                })
            }

        logger.info(f"[SSE->WS] Processando {len(unread_messages)} mensagens nao lidas")

        # Processa cada mensagem com contexto reconstruido
        # A primeira mensagem tera contexto reconstruido do historico
        mensagens_processadas = 0
        ultima_resposta = None

        for idx, msg in enumerate(unread_messages):
            logger.info(f"[SSE->WS] Processando mensagem {idx + 1}/{len(unread_messages)}")

            try:
                # Salva mensagem do motorista no historico
                save_driver_message(
                    telefone=cod_conversa,
                    mensagem=msg,
                    table_name=NEGOCIACAO_TABLE
                )
                logger.info(f"[SSE->WS] Mensagem do motorista salva no historico")

                # Processa com chatbot - from_sse=True para primeira mensagem para reconstruir contexto
                # Para mensagens subsequentes, from_sse=False pois ja temos contexto
                from_sse_flag = (idx == 0)
                resultado = processar_e_responder(cod_conversa, msg, from_sse=from_sse_flag)

                mensagens_processadas += 1
                ultima_resposta = resultado

            except Exception as msg_error:
                logger.error(f"[SSE->WS] Erro ao processar mensagem {idx + 1}: {str(msg_error)}")
                import traceback
                traceback.print_exc()

        logger.info(f"[SSE->WS] Transicao concluida: {mensagens_processadas}/{len(unread_messages)} mensagens processadas")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "success": True,
                "processado": True,
                "respondido": mensagens_processadas > 0,
                "mensagens_processadas": mensagens_processadas,
                "total_mensagens": len(unread_messages),
                "ultima_resposta": ultima_resposta
            })
        }

    except Exception as e:
        logger.error(f"[SSE->WS] Erro na transicao: {str(e)}")
        import traceback
        traceback.print_exc()

        return {
            "statusCode": 500,
            "body": json.dumps({
                "success": False,
                "error": str(e)
            })
        }

def processar_e_responder(cod_conversa: str, mensagem: Dict[str, Any], from_sse: bool = False) -> Dict[str, Any]:
    """
    Processa a mensagem com o chatbot e envia a resposta

    Input: cod_conversa (str), mensagem (Dict[str, Any]), from_sse (bool)
    Output: (Dict[str, Any]) - Resposta HTTP com resultado do processamento
    """
    try:
        resultado_proc = processar_com_chatbot(cod_conversa, mensagem, from_sse)
        
        if resultado_proc.get("status") == "error":
            erro_msg = resultado_proc.get("message", "")
            logger.error(f"[PROCESS] Erro no processamento do chatbot: {erro_msg}")

            texto = "Estamos tendo alguns problemas de disponibilidade por agora, envie mensagem daqui a pouco"

        else:
            texto_bruto = resultado_proc["resposta"]
            texto = sanitizar_texto_resposta(texto_bruto)

            try:
                increment_responses(cod_conversa)
                logger.info(f"[KPI] Resposta do motorista rastreada para {cod_conversa}")
            except Exception as kpi_error:
                logger.error(f"[KPI] Erro ao rastrear resposta: {str(kpi_error)}")

        response = dynamodb.query(
            TableName=TABLE_NAME,
            IndexName="telefone-index",
            KeyConditionExpression="telefone = :telefone",
            ExpressionAttributeValues={":telefone": {"S": cod_conversa}},
            ScanIndexForward=False,
            Limit=1,
        )

        item = response.get("Items", [])
        if item and item[0].get("resposta_audio", {}).get("S") == "sim":
            synth_response = synth_audio(texto)
            logger.info(f"Resposta audio: {synth_response}")
            nomeArquivo = synth_response["nomeArquivo"]
            logger.info(f"Nome arquivo: {nomeArquivo}")
            urlPublica = synth_response["urlPublica"]
            resposta = {
                "codConversa": cod_conversa,
                "mensagem": {
                    "texto": "",
                    "tipo": "audio",
                    "arquivo": {"nomeArquivo": nomeArquivo, "urlPublica": urlPublica},
                    "remetente": "assistente.virtual",
                    "telContato": cod_conversa,
                },
            }
        else:
            resposta = {
                "codConversa": cod_conversa,
                "mensagem": {
                    "texto": texto,
                    "tipo": "text",
                    "remetente": "assistente.virtual",
                    "telContato": cod_conversa,
                },
            }

        resultado_envio = enviar_resposta_via_ecs(cod_conversa, resposta)

        if resultado_envio["success"]:
            logger.info(f"[PROCESS] Resposta enviada com sucesso")

            try:
                logger.info(f"[MESSAGE-HISTORY] Salvando resposta do chatbot para {cod_conversa}")
                tipo_resposta = resposta.get('mensagem', {}).get('tipo', 'text')
                save_chatbot_response(
                    telefone=cod_conversa,
                    resposta_texto=texto,
                    resposta_tipo=tipo_resposta,
                    table_name=NEGOCIACAO_TABLE
                )
                logger.info(f"[MESSAGE-HISTORY] Resposta do chatbot salva no historico")
            except Exception as hist_error:
                logger.error(f"[MESSAGE-HISTORY] Erro ao salvar resposta do chatbot: {str(hist_error)}")

            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "success": True,
                        "processado": True,
                        "respondido": True,
                        "resposta": resposta,
                        "resultado_envio": resultado_envio,
                    }
                ),
            }
        else:
            logger.error(f"[PROCESS] Falha ao enviar resposta")
            return {
                "statusCode": 500,
                "body": json.dumps(
                    {
                        "success": False,
                        "message": "Falha ao enviar resposta",
                        "resultado_envio": resultado_envio,
                    }
                ),
            }
    except Exception as e:
        logger.error(f"[PROCESS] Erro ao processar e responder: {str(e)}")
        import traceback
        traceback.print_exc()
        
        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "success": False,
                    "error": str(e)
                }
            )
        }

def enviar_resposta_via_ecs(cod_conversa: str, resposta: Dict[str, Any]) -> Dict[str, Any]:
    """
    Envia resposta via ECS WebSocket server

    Input: cod_conversa (str), resposta (Dict[str, Any])
    Output: (Dict[str, Any]) - Resultado do envio com status de sucesso
    """
    ecs_url = get_websocket_url_with_fallback()

    if not ecs_url:
        ecs_url = os.environ.get("ECS_URL")

    if not ecs_url:
        logger.error("[ECS] Nao foi possivel resolver URL do ECS (dinamico ou variavel de ambiente)")
        return {"success": False, "message": "ECS URL nao configurado ou nao foi possivel resolver"}

    logger.info(f"[ECS] Usando WebSocket URL: {ecs_url}")

    try:
        logger.info(f"[ECS] Enviando resposta para ECS")

        mensagem = resposta["mensagem"]
        nomeArquivo = mensagem.get("arquivo", {}).get("nomeArquivo")
        urlPublica = mensagem.get("arquivo", {}).get("urlPublica")
        
        if mensagem["tipo"] == "audio":
            payload = {
            "codConversa": cod_conversa,
            "mensagem": {
                    "texto": "",
                    "tipo": "audio",
                    "remetente": mensagem["remetente"],
                    "arquivo": {"nomeArquivo": nomeArquivo, "urlPublica": urlPublica},
                    "remetente": "assistente.virtual",
                    "telContato": cod_conversa,
                    "isMensagemContato": False,
            }}
        if mensagem["tipo"] == "text":
            payload = {
            "codConversa": cod_conversa,
            "mensagem": {
                "texto": mensagem["texto"],
                "tipo": mensagem["tipo"],
                "remetente": mensagem["remetente"],
                "telContato": cod_conversa,
                "isMensagemContato": False,
            }}
        
        logger.info(f"[ECS] Payload: {payload}")

        response = retry_on_timeout(
            lambda: http.request(
                "POST",
                f"{ecs_url}/send",
                body=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                timeout=10.0,
            ),
            max_retries=3,
            operation_name="Send message to ECS",
            telefone=cod_conversa
        )

        success = response.status == 200
        logger.info(f"[ECS] Status: {response.status}")

        return {
            "success": success,
            "status_code": response.status,
            "via": "ecs_websocket",
            "data": json.loads(response.data.decode("utf-8")) if success else None,
        }

    except Exception as e:
        logger.error(f"[ECS] Erro ao enviar via ECS: {str(e)}")
        return {"success": False, "error": str(e)}

def processar_com_chatbot(cod_conversa: str, mensagem: Dict[str, Any], from_sse: bool = False) -> Dict[str, Any]:
    """
    Processa mensagem com o chatbot usando session management

    Input: cod_conversa (str), mensagem (Dict[str, Any]), from_sse (bool)
    Output: (Dict[str, Any]) - Resultado do processamento do chatbot
    """
    session_id, session_renewed = _get_or_create_session_id(cod_conversa)

    memory_id = _get_or_create_memory_id(cod_conversa)

    mensagem['codConversa'] = session_id

    session_attributes = _get_session_attributes_from_db(cod_conversa)

    session_attributes['memory_id'] = memory_id

    if session_renewed and from_sse:
        logger.info("[SESSION-RENEWAL] Sessao renovada - reconstruindo contexto do historico de mensagens")
        try:
            history_context = build_context_from_history(
                telefone=cod_conversa,
                table_name=NEGOCIACAO_TABLE
            )

            if history_context:
                texto_atual = mensagem.get('texto', '')
                mensagem['texto'] = f"{history_context}\n\n===== NOVA MENSAGEM DO MOTORISTA =====\n{texto_atual}"

                logger.info(f"[SESSION-RENEWAL] Contexto reconstruido do historico")
                logger.info(f"[SESSION-RENEWAL] Tamanho do contexto do historico: {len(history_context)} chars")
                logger.info(f"[SESSION-RENEWAL] Tamanho total da mensagem: {len(mensagem['texto'])} chars")
            else:
                logger.warning("[SESSION-RENEWAL] Nenhum historico de mensagens encontrado para reconstruir contexto")

        except Exception as history_error:
            logger.error(f"[SESSION-RENEWAL] Erro ao reconstruir contexto do historico: {str(history_error)}")
            import traceback
            traceback.print_exc()
    elif session_renewed and not from_sse:
        logger.info("[SESSION-RENEWAL] Sessao renovada mas from_sse=False - pulando reconstrucao de contexto")
        logger.info("[SESSION-RENEWAL] Reconstrucao de contexto so executa para reconexoes SSE->Websocket")
    elif not session_renewed:
        logger.info("[SESSION-RENEWAL] Sessao nao renovada - usando sessao existente, sem necessidade de reconstrucao de contexto")

    if mensagem.get('mensagemResposta'):
        logger.info("[OFFER-CONTEXT] Mensagem respondendo a mensagem anterior detectada")
        logger.info(f"[OFFER-CONTEXT] Buscando contexto de oferta para telefone: {cod_conversa}")

        try:
            mensagem_resposta = mensagem.get('mensagemResposta', {})
            data_hora_envio = mensagem_resposta.get('dataHoraEnvio')

            logger.info(f"[OFFER-CONTEXT] dataHoraEnvio da mensagem respondida: {data_hora_envio}")

            carga_id = _buscar_carga_id_da_negociacao(cod_conversa, data_hora_envio)

            if carga_id:
                logger.info(f"[OFFER-CONTEXT] carga_id encontrado: {carga_id}")

                oferta_data = _buscar_dados_oferta_por_carga_id(carga_id)

                if oferta_data:
                    logger.info(f"[OFFER-CONTEXT] Dados da oferta encontrados")

                    contexto_oferta = _construir_contexto_oferta(oferta_data)

                    session_attributes.update(contexto_oferta)

                    logger.info(f"[OFFER-CONTEXT] Contexto da oferta adicionado aos session_attributes")
                    logger.info(f"[OFFER-CONTEXT] Campos adicionados: {list(contexto_oferta.keys())}")
                else:
                    logger.warning(f"[OFFER-CONTEXT] Nao foi possivel buscar dados da oferta para carga_id {carga_id}")
            else:
                logger.info("[OFFER-CONTEXT] Nenhum carga_id encontrado na negociacao")

        except Exception as e:
            logger.error(f"[OFFER-CONTEXT] Erro ao buscar contexto de oferta: {str(e)}")
            import traceback
            traceback.print_exc()

    payload = {
        'mensagem': mensagem,
        'sessionAttributes': session_attributes
    }

    logger.info(f"[CHATBOT] Chamando chatbot com session_id: {session_id}, memory_id: {memory_id}")
    logger.info(f"[CHATBOT] Quantidade de session attributes: {len(session_attributes)}")

    response = lambda_client.invoke(
        FunctionName="chatbot",
        InvocationType="RequestResponse",
        Payload=json.dumps(payload),
    )

    result = json.loads(response["Payload"].read())

    logger.info(f"[CHATBOT] Resposta recebida do chatbot")
    return result

def transcrever_audio(msg):
    """
    Transcreve arquivo de audio usando AWS Transcribe

    Input: msg (dict) - Mensagem com nomeArquivo do audio
    Output: (str) - Texto transcrito ou None em caso de erro
    """
    logger.info(f"[AUDIO] Transcrevendo audio: {msg}")
    if not auth_cookie_cache:
        sucesso, token, erro = obter_token_do_parameter_store()
        if not sucesso:
            logger.error(f"[AUDIO] Erro ao obter token: {erro}")
            return None

    nome_arquivo = msg.get("nomeArquivo")
    headers = {}
    headers["Cookie"] = auth_cookie_cache

    if nome_arquivo:
        try:
            base_url_download = "https://api-gateway.rodosafra.net:8760/api/chat/arquivo"

            url_download = f"{base_url_download}?nomeArquivo={nome_arquivo}"
            logger.info(f"[AUDIO] Baixando arquivo de audio: {url_download}")

            # Faz o download do arquivo with retry logic (up to 3 attempts on timeout)
            response = retry_on_timeout(
                lambda: http.request(
                    "GET", url_download, headers=headers, timeout=20.0
                ),
                max_retries=3,
                operation_name="Download audio file"
            )

            if response.status != 200:
                logger.info(f"Falha ao baixar arquivo: HTTP {response.status}")
                raise Exception(f"Falha ao baixar arquivo: HTTP {response.status}")

            # Nome do arquivo temporário e bucket destino
            bucket_destino = "transcribe-bucket-rodosafra"
            caminho_s3 = f"audios/{nome_arquivo}"
            
            conteudo = response.data
            if not conteudo:
                raise Exception("Resposta HTTP não possui corpo (conteudo é None).")

            logger.info(f"[AUDIO] Arquivo baixado com sucesso")
            try:
                conteudo_decodificado = base64.b64decode(conteudo, validate=True)
                conteudo = conteudo_decodificado
                logger.info("[AUDIO] Conteudo decodificado de Base64 com sucesso")
            except (base64.binascii.Error, ValueError):
                logger.info("[AUDIO] Conteudo nao esta em Base64, mantendo bytes originais")

            s3_client = boto3.client("s3")
            s3_client.put_object(
                Bucket=bucket_destino, Key=caminho_s3, Body=conteudo)

            logger.info(f"[AUDIO] Arquivo {nome_arquivo} salvo em s3://{bucket_destino}/{caminho_s3}")
            
            transcribe_client = boto3.client("transcribe")
            job_name = f"transcribe-{nome_arquivo.replace('.', '-')}"

            media_format = "mp3"
            if nome_arquivo.lower().endswith(".wav"):
                media_format = "wav"
            elif nome_arquivo.lower().endswith(".ogg"):
                media_format = "ogg"

            s3_uri = f"s3://{bucket_destino}/{caminho_s3}"
            output_key = f"transcricao/{job_name}.json"
            logger.info(f"[AUDIO] Iniciando job do Transcribe: {job_name}")

            transcribe_client.start_transcription_job(
                TranscriptionJobName=job_name,
                Media={"MediaFileUri": s3_uri},
                MediaFormat=media_format,
                LanguageCode="pt-BR",
                OutputBucketName=bucket_destino,
                OutputKey=output_key,)

            while True:
                status = transcribe_client.get_transcription_job(TranscriptionJobName=job_name)
                job_status = status["TranscriptionJob"]["TranscriptionJobStatus"]

                if job_status in ["COMPLETED", "FAILED"]:
                    logger.info(f"[AUDIO] Transcribe status: {job_status}")
                    break
                time.sleep(2)

            if job_status == "COMPLETED":
                transcript_uri = status["TranscriptionJob"]["Transcript"]["TranscriptFileUri"]
                logger.info(f"[AUDIO] Transcricao disponivel em: {transcript_uri}")

                s3 = boto3.client("s3")
                logger.info(f"[AUDIO] Baixando transcricao de s3://{bucket_destino}/{output_key}")
                response = s3.get_object(Bucket=bucket_destino, Key=output_key)
                transcript_data = json.loads(response["Body"].read().decode("utf-8"))

                texto_transcrito = "Texto Transcrito:"
                texto_transcrito += transcript_data["results"]["transcripts"][0]["transcript"]
                logger.info(f"[AUDIO] Texto transcrito: {texto_transcrito}")
                msg["texto"] = texto_transcrito
                return texto_transcrito
            elif job_status == "FAILED":
                failure_reason = status["TranscriptionJob"].get("FailureReason", "Motivo desconhecido")
                logger.error(f"[AUDIO] Transcribe falhou: {failure_reason}")
                logger.warning("[AUDIO] Falha no job do Transcribe, mantendo texto vazio")
                text = "(audio nao pode ser transcrito)"
                msg["texto"] = text
                return text

        except Exception as e:
            logger.error(f"[AUDIO] Erro ao baixar/transcrever audio: {str(e)}")

def synth_audio(text):
    """
    Sintetiza audio a partir de texto usando AWS Polly

    Input: text (str) - Texto para sintetizar
    Output: (dict) - Dicionario com nomeArquivo e urlPublica do audio gerado
    """
    if not auth_cookie_cache:
        sucesso, token, erro = obter_token_do_parameter_store()
        if not sucesso:
            logger.error(f"[SYNTH] Erro ao obter token: {erro}")
            return None
            
    REGION = "us-east-1"
    BUCKET_NAME = "transcribe-bucket-rodosafra"
    VOICE_ID = "Camila"
    TEXT = text

    polly = boto3.client("polly", region_name=REGION)

    response = polly.synthesize_speech(
        Text=TEXT, OutputFormat="ogg_opus", VoiceId=VOICE_ID
    )

    folder = "polly/"
    filename = f"{folder}voz-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.ogg"

    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=filename,
        Body=response["AudioStream"].read(),
        ContentType="audio/ogg",
    )

    obj = s3.get_object(Bucket=BUCKET_NAME, Key=filename)
    conteudo = obj["Body"].read()

    arquivo_b64 = base64.b64encode(conteudo).decode("utf-8")

    nome = os.path.basename(filename)
    extensao = nome.split(".")[-1]

    payload = {
        "arquivo": arquivo_b64,
        "extensao": extensao,
        "nome": nome,
        "tipoMensagem": "AUDIO",
    }

    api_url = "https://api-gateway.rodosafra.net:8760/api/chat/arquivo"
    headers = {"Cookie": auth_cookie_cache, "Content-Type": "application/json"}

    response = retry_on_timeout(
        lambda: http.request("POST", api_url, body=json.dumps(payload), headers=headers),
        max_retries=3,
        operation_name="Upload audio file"
    )

    logger.info(f"[SYNTH] Status HTTP: {response.status}")
    try:
        resposta_json = json.loads(response.data.decode("utf-8"))
    except Exception:
        resposta_json = response.data.decode("utf-8")

    if 200 <= response.status < 300:
        logger.info(f"[SYNTH] Upload realizado com sucesso. Resposta: {resposta_json}")
    else:
        logger.error(f"[SYNTH] Erro no upload. Resposta: {resposta_json}")

    return {
        "nomeArquivo": resposta_json.get("nomeArquivo", "") if isinstance(resposta_json, dict) else "",
        "urlPublica": resposta_json.get("urlPublica", "") if isinstance(resposta_json, dict) else ""
    }

def tratar_localizacao(msg):
    """
    Trata mensagens do tipo LOCATION formatando coordenadas e endereco

    Input: msg (dict) - Mensagem com dados de localizacao (latitude, longitude, endereco)
    Output: (str) - Descricao formatada da localizacao
    """
    loc = msg.get("localizacao", {})
    lat = loc.get("latitude")
    lon = loc.get("longitude")
    if lat and lon:
        endereco = loc.get("endereco")
        if endereco:
            return f"Motorista enviou a seguinte localização: (Endereço:{endereco}, Latitude:{lat}, Longitude:{lon})"
        else:
            endereco_aprox = aproximar_endereco_osm(lat, lon)
            return f"Motorista enviou a seguinte localização: (Endereço:{endereco_aprox}, Latitude:{lat}, Longitude:{lon})"

    return "[Localização recebida (dados incompletos)]"

def tratar_documento(msg):
    """
    Trata mensagens do tipo DOCUMENT fazendo download, salvando no S3 e usando OCR para extrair texto

    Input: msg (dict) - Mensagem com nomeArquivo do documento
    Output: (str) - Descricao do documento com texto extraido pelo OCR
    """
    if not auth_cookie_cache:
        sucesso, token, erro = obter_token_do_parameter_store()
        if not sucesso:
            logger.error(f"[DOC] Erro ao obter token: {erro}")
            return None

    nome_arquivo = msg.get("nomeArquivo")
    texto = msg.get("texto", "")
    headers = {"Cookie": auth_cookie_cache}

    try:
        base_url_download = "https://api-gateway.rodosafra.net:8760/api/chat/arquivo"
        url_download = f"{base_url_download}?nomeArquivo={nome_arquivo}"
        logger.info(f"[DOC] Baixando documento: {url_download}")

        # Wrap API call with retry logic (up to 3 attempts on timeout)
        response = retry_on_timeout(
            lambda: http.request("GET", url_download, headers=headers, timeout=20.0),
            max_retries=3,
            operation_name="Download document"
        )

        if response.status != 200:
            raise Exception(f"Falha ao baixar arquivo: HTTP {response.status}")

        conteudo = response.data
        if not conteudo:
            raise Exception("Resposta HTTP não possui corpo (conteúdo é None).")

        try:
            conteudo = base64.b64decode(conteudo, validate=True)
            logger.info("Conteúdo decodificado de Base64 com sucesso.")
        except (base64.binascii.Error, ValueError):
            logger.info("Conteúdo não está em Base64, mantendo bytes originais.")

        bucket_destino = "attachments-bucket-rodosafra"
        caminho_s3 = f"documentos/{nome_arquivo}"

        s3_client = boto3.client("s3")
        s3_client.put_object(Bucket=bucket_destino, Key=caminho_s3, Body=conteudo)
        logger.info(f"Arquivo salvo em s3://{bucket_destino}/{caminho_s3}")
        
        file_object = s3.get_object(Bucket=bucket_destino, Key=caminho_s3)
        file_bytes = file_object['Body'].read()
        file_extension = caminho_s3.split('.')[-1].lower()
        filename = caminho_s3.split('/')[-1].split('.')[0]

        
        descricao = f"[Documento recebido: {nome_arquivo}]"
        if texto:
            descricao += f"\nMensagem associada digitada pelo motorista: {texto}"

        MODEL_ID = 'us.anthropic.claude-3-5-sonnet-20241022-v2:0'
        TEMPERATURE = 0.0
        PROMPT = """
            Você é um assistente especializado em leitura e interpretação de documentos oficiais brasileiros.

            Entrada: um documento em formato PDF ou DOCX.  
            O documento pode ser de qualquer tipo — CNH digitalizada, RG escaneado, contrato, comprovante, formulário, termo, ou outro texto.

            Sua tarefa:
            1. Leia e analise todo o conteúdo textual do documento.  
            2. Identifique e descreva **todas as informações relevantes** contidas nele, como:
            - Tipo de documento (ex: CNH, RG, contrato, nota fiscal, etc.);
            - Dados pessoais (nome, CPF, RG, data de nascimento, endereço, etc.);
            - Dados de empresa (razão social, CNPJ, inscrição estadual, endereço, etc.);
            - Informações contratuais (partes envolvidas, valores, prazos, cláusulas importantes);
            - Datas, números, códigos, assinaturas ou observações relevantes.
            3. Explique **o propósito do documento**, ou seja, para que ele parece ter sido feito.
            4. Se o documento tiver várias seções, descreva cada uma de forma resumida, mas completa.
            5. Caso o conteúdo esteja incompleto, ilegível ou não seja possível identificar claramente o tipo de documento, diga isso explicitamente.
            6. Seja **detalhado, objetivo e claro**.  
            7. Responda **em texto natural**, sem JSON ou listas de chave-valor.

            Exemplo de resposta esperada:
            > O documento é um contrato de prestação de serviços firmado entre a empresa Alfa Logística Ltda e o motorista João da Silva.  
            > Contém informações pessoais do motorista, incluindo CPF 123.456.789-00 e CNH categoria D.  
            > O contrato estabelece prazo de 12 meses, com renovação automática, e pagamento mensal de R$ 3.500.  
            > Há assinaturas das partes e uma cláusula sobre rescisão antecipada.
            """

        messages = [
                {
                    'role': 'user',
                    'content': [
                        {
                            'text': PROMPT
                        },
                        {
                            'document': {
                                'format': file_extension,
                                'name': filename,
                                'source': {
                                    'bytes': file_bytes
                                }
                            }
                        }
                    ]
                }
            ]
        
        bedrock_response = bedrock.converse(
            modelId=MODEL_ID,
            messages=messages,
            inferenceConfig={
                'temperature': TEMPERATURE,
                'maxTokens': 4096
            }
        )
        
        ocr_text = bedrock_response['output']['message']['content'][0]['text']
        descricao += f"\nElementos inferidos pelo OCR do documento: {ocr_text}"

        return descricao

    except Exception as e:
        logger.error(f"Erro ao baixar/armazenar documento: {str(e)}")
        return f"[Erro ao processar documento: {str(e)}]"

def tratar_imagem(msg):
    """
    Trata mensagens do tipo IMAGE fazendo download, salvando no S3 e usando OCR para extrair texto

    Input: msg (dict) - Mensagem com nomeArquivo da imagem
    Output: (str) - Descricao da imagem com texto extraido pelo OCR
    """
    if not auth_cookie_cache:
        sucesso, token, erro = obter_token_do_parameter_store()
        if not sucesso:
            logger.error(f"[IMAGE] Erro ao obter token: {erro}")
            return None
            
    nome_arquivo = msg.get("nomeArquivo")
    texto = msg.get("texto", "")
    headers = {"Cookie": auth_cookie_cache}

    if not nome_arquivo:
        return "[Imagem recebida sem nome]"

    try:
        base_url_download = "https://api-gateway.rodosafra.net:8760/api/chat/arquivo"
        url_download = f"{base_url_download}?nomeArquivo={nome_arquivo}"
        logger.info(f"[IMAGE] Baixando imagem: {url_download}")

        # Wrap API call with retry logic (up to 3 attempts on timeout)
        response = retry_on_timeout(
            lambda: http.request("GET", url_download, headers=headers, timeout=20.0),
            max_retries=3,
            operation_name="Download image"
        )

        if response.status != 200:
            raise Exception(f"Falha ao baixar imagem: HTTP {response.status}")

        conteudo = response.data
        if not conteudo:
            raise Exception("Resposta HTTP não possui corpo (conteúdo é None).")

        try:
            conteudo = base64.b64decode(conteudo, validate=True)
            logger.info("Conteúdo decodificado de Base64 com sucesso.")
        except (base64.binascii.Error, ValueError):
            logger.info("Conteúdo não está em Base64, mantendo bytes originais.")

        bucket_destino = "attachments-bucket-rodosafra"
        caminho_s3 = f"imagens/{nome_arquivo}"

        s3_client = boto3.client("s3")
        s3_client.put_object(Bucket=bucket_destino, Key=caminho_s3, Body=conteudo)
        logger.info(f"Imagem salva em s3://{bucket_destino}/{caminho_s3}")

        file_object = s3.get_object(Bucket=bucket_destino, Key=caminho_s3)
        file_bytes = file_object['Body'].read()
        file_extension = caminho_s3.split('.')[-1].lower()
        filename = caminho_s3.split('/')[-1].split('.')[0]

        descricao = f"[Imagem recebida: {nome_arquivo}]"
        if texto:
            descricao += f"\nMensagem associada digitada pelo motorista: {texto}"

        MODEL_ID = 'us.anthropic.claude-3-5-sonnet-20241022-v2:0'
        TEMPERATURE = 0.0
        PROMPT = """
            Você é um assistente especialista em análise de imagens e leitura de documentos oficiais brasileiros.

            Entrada: uma imagem (pode ser um documento ou qualquer outro tipo de foto).

            Sua tarefa:
            1. Observe cuidadosamente a imagem recebida.
            2. Se for um documento (como CNH, RG, CPF, CRLV-e, ANTT, ou similar), extraia **todas as informações textuais e visuais** relevantes.  
            Inclua nome, número do documento, CPF, datas, categoria, órgão emissor, observações e qualquer outro dado legível.
            3. Se não for um documento, descreva **tudo o que você consegue perceber** na imagem:  
            objetos, pessoas, cores, cenários, texto visível, logotipos, expressões, e contexto provável.
            4. Se a imagem estiver borrada, cortada ou ilegível, explique o que você conseguiu perceber e o que não foi possível identificar.
            5. Seja **detalhado, preciso e direto**.  
            6. Responda apenas com o texto descritivo — **não use formato JSON** nem listas de chave-valor.

            Exemplo de resposta esperada:
            > A imagem mostra uma Carteira Nacional de Habilitação brasileira em nome de João da Silva, nascido em 12 de março de 1988, CPF 123.456.789-00, categoria B, validade até 2028. O documento foi emitido pelo DETRAN-SP.  
            > Há também um pequeno selo no canto inferior direito e fundo com padrões de segurança.
            """
        
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "text": PROMPT
                    },
                    {
                        "image": {
                            "format": file_extension,
                            "source": {
                                "bytes": file_bytes
                            }
                        }
                    }
                ]
            }
        ]

        bedrock_response = bedrock.converse(
            modelId=MODEL_ID,
            messages=messages,
            inferenceConfig={
                'temperature': TEMPERATURE,
                'maxTokens': 4096
            }
        )
        
        ocr_text = bedrock_response['output']['message']['content'][0]['text']
        if ocr_text == "I apologize, but I cannot and should not assist with or provide analysis of personal identification documents, as this could enable identity theft or fraud. Sharing or exposing personal identification documents online can be dangerous and may be illegal. I recommend keeping such documents private and secure.":
            return "Documento de identificação. Você não pode lê-lo. Peça pro motorista digitar o número do CPF."
        descricao += f"\nElementos inferidos pelo OCR da imagem: {ocr_text}"
        
        return descricao

    except Exception as e:
        logger.error(f"Erro ao baixar/armazenar imagem: {str(e)}")
        return f"[Erro ao processar imagem: {str(e)}]"    

def lambda_handler(event, context):
    """
    Handler principal com suporte a acumulacao de mensagens com debouncing

    Input: event (dict), context (object)
    Output: (dict) - Resposta HTTP com resultado do processamento
    """
    try:
        if isinstance(event, str):
            event = json.loads(event)
        
        # Verifica se eh um evento de processamento agendado
        if event.get('acao') == 'processar_mensagens_acumuladas':
            return processar_mensagens_acumuladas_handler(event, context)
        
        # Caso contrario, eh uma mensagem nova do ECS
        if "body" in event:
            body = (
                json.loads(event["body"])
                if isinstance(event["body"], str)
                else event["body"]
            )
        else:
            body = event

        logger.info(f"[HANDLER] Event recebido: {json.dumps(event)}")

        cod_conversa_raw = body.get("codConversa")
        mensagem = body.get("mensagem", {})

        if not cod_conversa_raw or not mensagem:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "success": False,
                        "message": "codConversa e mensagem sao obrigatorios",
                    }
                ),
            }

        cod_conversa = normalizar_telefone(cod_conversa_raw)
        logger.info(f"[HANDLER] Conversa: {cod_conversa} (original: {cod_conversa_raw})")

        # Detecta transicao SSE->WebSocket e processa separadamente
        if mensagem.get("tipo") == "SSE_TO_WEBSOCKET_TRANSITION":
            logger.info(f"[HANDLER] Detectada transicao SSE->WebSocket para {cod_conversa}")
            return handle_sse_to_websocket_transition(cod_conversa)

        from_sse = body.get("from_sse", False)
        if from_sse:
            logger.info(f"[SSE] Mensagem recebida do SSE (passive flow)")

        if mensagem.get("isMensagemContato") == False:
            logger.info(f"[HANDLER] Nao e mensagem do contato, nao responder")
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "success": True,
                        "processado": True,
                        "respondido": False,
                        "motivo": "Nao e mensagem do contato",
                    }
                ),
            }

        # Armazena mensagem e verifica se deve processar ou aguardar
        resultado_armazenamento = armazenar_mensagem_pendente(cod_conversa, mensagem)

        if not resultado_armazenamento['deve_processar']:
            # Mensagem armazenada, aguardando timer
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "success": True,
                    "processado": True,
                    "respondido": False,
                    "motivo": "Mensagem armazenada para acumulacao",
                    "is_first_message": resultado_armazenamento['is_first_message'],
                    "mensagens_acumuladas": resultado_armazenamento['mensagens_acumuladas'],
                    "timer_resetado": resultado_armazenamento.get('timer_resetado', False)
                })
            }

        # Se chegou aqui, deve processar imediatamente (erro ou outro motivo)
        return processar_e_responder(cod_conversa, mensagem, from_sse)
        
    except Exception as e:
        logger.error(f"[HANDLER] Erro: {str(e)}")
        import traceback
        traceback.print_exc()

        return {
            "statusCode": 500,
            "body": json.dumps({"success": False, "error": str(e)}),
        }

def aproximar_endereco_osm(lat, lon):
    """
    Aproxima endereco usando coordenadas de latitude e longitude via OpenStreetMap

    Input: lat (float), lon (float) - Coordenadas geograficas
    Output: (str) - Endereco aproximado ou None em caso de erro
    """
    base_url = "https://nominatim.openstreetmap.org/reverse"

    params = {
        "lat": lat,
        "lon": lon,
        "format": "json",
        "addressdetails": 1
    }

    url = base_url + "?" + urllib.parse.urlencode(params)

    headers = {
        "User-Agent": "rodosafra-assistente-virtual/1.0"
    }

    request = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(request) as response:
            data = json.loads(response.read().decode())
            logger.info(f"[OSM] Endereco aproximado: {data.get('display_name')}")
            return data.get("display_name")
    except Exception as e:
        logger.error(f"[OSM] Erro ao aproximar endereco: {e}")
        return None