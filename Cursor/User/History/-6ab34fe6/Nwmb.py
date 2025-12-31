"""
Lambda principal do chatbot Rodosafra
Responsavel por gerenciar sessoes, memoria e invocar o agente Bedrock
"""
import os
import json
import boto3
import logging
import decimal
import uuid
import datetime as dt
from typing import Any, Dict, List, Optional
from boto3.dynamodb.conditions import Key, Attr

logger = logging.getLogger()
logger.setLevel(logging.INFO)

bedrock = boto3.client("bedrock-agent-runtime")
dynamodb = boto3.resource("dynamodb")

MOTORISTA_TABLE = os.environ.get("MOTORISTA_TABLE", "motoristas")
VEICULO_TABLE = os.environ.get("VEICULO_TABLE", "veiculos")
SESSOES_TABLE = os.environ.get("SESSOES_TABLE", "motoristas_sessoes")
NEGOCIACAO_TABLE = os.environ.get("NEGOCIACAO_TABLE", "negociacao")

AGENT_ID = "NWX9PJAPHT"
AGENT_ALIAS = "4GGFGMBABJ"


def normalizar_telefone(telefone: str) -> str:
    """
    Normaliza numero de telefone para formato padrao com codigo do pais (55)

    Input: telefone (str) - Numero em qualquer formato
    Output: (str) - Telefone normalizado com 13 digitos (55 + DDD + numero)
    """
    if not telefone:
        logger.warning("[VALIDACAO] Telefone vazio fornecido para normalizacao")
        return telefone

    telefone_limpo = ''.join(filter(str.isdigit, str(telefone)))

    if telefone_limpo.startswith('55') and len(telefone_limpo) == 13:
        return telefone_limpo

    if len(telefone_limpo) == 11:
        telefone_normalizado = f"55{telefone_limpo}"
        logger.info(f"[NORMALIZACAO] Telefone normalizado de 11 para 13 digitos: {telefone_normalizado}")
        return telefone_normalizado

    if len(telefone_limpo) == 13 and not telefone_limpo.startswith('55'):
        logger.warning(f"[VALIDACAO] Telefone com 13 digitos mas nao comeca com 55: {telefone_limpo}")
        return telefone_limpo

    if len(telefone_limpo) > 13:
        logger.warning(f"[VALIDACAO] Telefone com excesso de digitos: {len(telefone_limpo)}")
        if telefone_limpo.endswith(telefone_limpo[-13:]) and telefone_limpo[-13:-11] == '55':
            return telefone_limpo[-13:]
        return telefone_limpo

    if len(telefone_limpo) < 11:
        logger.error(f"[VALIDACAO] Telefone com digitos insuficientes: {len(telefone_limpo)}")
        return telefone_limpo

    logger.warning(f"[VALIDACAO] Telefone com padrao desconhecido: {telefone_limpo}")
    return telefone_limpo


class DecimalEncoder(json.JSONEncoder):
    """Encoder customizado para converter Decimal em float no JSON"""
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return float(o)
        return super().default(o)


def _json_dumps(obj: Any) -> str:
    """
    Serializa objeto para JSON com suporte a Decimal

    Input: obj (Any) - Objeto a ser serializado
    Output: (str) - String JSON
    """
    return json.dumps(obj, ensure_ascii=False, cls=DecimalEncoder)


def _safe_str(v: Any) -> str:
    """
    Converte qualquer valor para string de forma segura

    Input: v (Any) - Valor a ser convertido
    Output: (str) - String representando o valor
    """
    if v is None:
        return ""
    if isinstance(v, (int, float, decimal.Decimal)):
        return str(v)
    return str(v)


def _iso_utc(d: Optional[dt.datetime] = None) -> str:
    """
    Retorna timestamp ISO 8601 em UTC

    Input: d (Optional[datetime]) - Data/hora ou None para usar agora
    Output: (str) - Timestamp no formato ISO 8601
    """
    d = d or dt.datetime.now(dt.timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_session_timestamp_from_negociacao(telefone: str) -> tuple:
    """
    Busca tempo_sessao e session_id mais recentes da tabela negociacao

    Input: telefone (str) - Numero do telefone
    Output: (tuple) - (tempo_sessao, session_id) ou (None, None) se nao encontrado
    """
    if not telefone:
        logger.warning("[SESSAO] Busca de sessao ignorada - telefone vazio")
        return None, None

    try:
        table = dynamodb.Table(NEGOCIACAO_TABLE)

        response = table.query(
            KeyConditionExpression=Key('telefone').eq(telefone),
            ScanIndexForward=False,
            Limit=1,
            ProjectionExpression='tempo_sessao, session_id'
        )

        items = response.get("Items", [])

        if items and 'tempo_sessao' in items[0]:
            tempo_sessao = str(items[0]['tempo_sessao'])
            session_id = str(items[0].get('session_id', '')) if items[0].get('session_id') else None
            logger.info(f"[SESSAO] Sessao encontrada para telefone {telefone}")
            return tempo_sessao, session_id
        else:
            logger.info(f"[SESSAO] Nenhuma sessao encontrada para telefone {telefone}")
            return None, None

    except Exception as e:
        logger.error(f"[ERRO] Falha ao buscar sessao: {type(e).__name__} - {str(e)}")
        return None, None


def _parse_timestamp_to_unix(tempo_str: str) -> int:
    """
    Converte timestamp para Unix timestamp (segundos)
    Suporta formatos ISO 8601, Unix timestamp e formato compacto legado

    Input: tempo_str (str) - Timestamp em qualquer formato suportado
    Output: (int) - Unix timestamp em segundos
    """
    if not tempo_str:
        raise ValueError("Timestamp string vazio")

    if len(tempo_str) <= 10 and tempo_str.isdigit():
        return int(tempo_str)

    if 'T' in tempo_str and tempo_str.endswith('Z'):
        try:
            dt_obj = dt.datetime.fromisoformat(tempo_str.replace('Z', '+00:00'))
            return int(dt_obj.timestamp())
        except Exception as e:
            logger.error(f"[ERRO] Falha ao parsear timestamp ISO: {tempo_str}")
            raise ValueError(f"Formato ISO invalido: {tempo_str}")

    try:
        year = int(tempo_str[0:4])
        month = int(tempo_str[4:6])
        day = int(tempo_str[6:8])
        hour = int(tempo_str[8:10])
        minute = int(tempo_str[10:12])
        second = int(tempo_str[12:14])
        microsecond = int(tempo_str[14:20]) if len(tempo_str) >= 20 else 0

        dt_obj = dt.datetime(year, month, day, hour, minute, second, microsecond, tzinfo=dt.timezone.utc)
        return int(dt_obj.timestamp())
    except Exception as e:
        logger.error(f"[ERRO] Falha ao parsear timestamp numerico: {tempo_str}")
        raise ValueError(f"Formato de timestamp invalido: {tempo_str}")


def _is_session_valid(tempo_sessao_str: str) -> bool:
    """
    Verifica se a sessao ainda e valida (menos de 1 hora)

    Input: tempo_sessao_str (str) - Timestamp da sessao
    Output: (bool) - True se sessao valida, False caso contrario
    """
    try:
        session_timestamp = _parse_timestamp_to_unix(tempo_sessao_str)
        current_timestamp = int(dt.datetime.now(dt.timezone.utc).timestamp())

        diff_seconds = current_timestamp - session_timestamp

        is_valid = diff_seconds < 3600

        logger.info(f"[SESSAO] Validacao - Diferenca: {diff_seconds}s - Valida: {is_valid}")

        return is_valid
    except Exception as e:
        logger.error(f"[ERRO] Falha na validacao de sessao: {type(e).__name__}")
        return False


def _create_new_session_id(telefone: str) -> tuple[str, str, str]:
    """
    Cria novo session_id, tempo_sessao e negociacao_iniciada_em

    Input: telefone (str) - Numero do telefone
    Output: (tuple) - (session_id, tempo_sessao, negociacao_iniciada_em)
    """
    now = dt.datetime.now(dt.timezone.utc)

    tempo_sessao = now.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    session_id = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    negociacao_iniciada_em = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    logger.info(f"[SESSAO] Nova sessao criada: {session_id}")

    return session_id, tempo_sessao, negociacao_iniciada_em


def _save_session_timestamp_to_negociacao(telefone: str, tempo_sessao: str, session_id: str, negociacao_iniciada_em: str = None) -> bool:
    """
    Salva tempo_sessao, session_id e negociacao_iniciada_em na tabela negociacao

    Input: telefone (str), tempo_sessao (str), session_id (str), negociacao_iniciada_em (str)
    Output: (bool) - True se salvo com sucesso, False caso contrario
    """
    if not telefone or not tempo_sessao or not session_id:
        logger.warning("[SESSAO] Salvamento ignorado - parametros invalidos")
        return False

    try:
        table = dynamodb.Table(NEGOCIACAO_TABLE)

        item = {
            "telefone": telefone,
            "tempo_sessao": tempo_sessao,
            "session_id": session_id
        }

        if negociacao_iniciada_em:
            item["negociacao_iniciada_em"] = negociacao_iniciada_em

        table.put_item(Item=item)

        logger.info(f"[SESSAO] Sessao salva com sucesso para telefone {telefone}")

        return True
    except Exception as e:
        logger.error(f"[ERRO] Falha ao salvar sessao: {type(e).__name__} - {str(e)}")
        return False


def _get_or_create_session_id(telefone: str) -> str:
    """
    Gerencia sessao: busca sessao existente ou cria nova se expirada

    Input: telefone (str) - Numero do telefone
    Output: (str) - Session ID valido
    """
    if not telefone:
        logger.warning("[SESSAO] Gerenciamento ignorado - telefone vazio")
        return str(uuid.uuid4())

    logger.info(f"[SESSAO] Iniciando gerenciamento de sessao para {telefone}")

    existing_tempo_sessao, existing_session_id = _get_session_timestamp_from_negociacao(telefone)

    now = dt.datetime.now(dt.timezone.utc)
    current_tempo_sessao = now.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    current_timestamp = int(now.timestamp())

    if not existing_tempo_sessao or not existing_session_id:
        logger.info("[SESSAO] Criando nova sessao - nenhuma sessao existente")
        new_session_id = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        negociacao_iniciada_em = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        _save_session_timestamp_to_negociacao(telefone, current_tempo_sessao, new_session_id, negociacao_iniciada_em)
        return new_session_id

    try:
        session_age_seconds = current_timestamp - _parse_timestamp_to_unix(existing_session_id)
    except Exception as e:
        logger.error(f"[ERRO] Falha ao calcular idade da sessao: {str(e)}")
        new_session_id = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        negociacao_iniciada_em = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        _save_session_timestamp_to_negociacao(telefone, current_tempo_sessao, new_session_id, negociacao_iniciada_em)
        return new_session_id

    if session_age_seconds < 3600:
        if existing_tempo_sessao == current_tempo_sessao:
            logger.info(f"[SESSAO] Reusando sessao existente - mesma hora ({session_age_seconds}s)")
            return existing_session_id
        else:
            logger.info(f"[SESSAO] Reusando session_id - nova hora ({session_age_seconds}s)")
            _save_session_timestamp_to_negociacao(telefone, current_tempo_sessao, existing_session_id)
            return existing_session_id

    logger.info(f"[SESSAO] Criando nova sessao - sessao expirada ({session_age_seconds}s)")
    new_session_id = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    negociacao_iniciada_em = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    _save_session_timestamp_to_negociacao(telefone, current_tempo_sessao, new_session_id, negociacao_iniciada_em)
    return new_session_id


def _normalize_tempo_memoria_to_iso(tempo_memoria: str) -> str:
    """
    Normaliza tempo_memoria para formato ISO 8601 se estiver em formato compacto legado

    Input: tempo_memoria (str) - Timestamp em qualquer formato
    Output: (str) - Timestamp em formato ISO 8601
    """
    if not tempo_memoria:
        return tempo_memoria

    tempo_str = str(tempo_memoria).strip()

    if 'T' in tempo_str and '-' in tempo_str:
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

            dt_obj = dt.datetime(year, month, day, hour, minute, second, microsecond, tzinfo=dt.timezone.utc)
            iso_str = dt_obj.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

            logger.info(f"[MEMORIA] Tempo normalizado para ISO 8601")

            return iso_str

        except Exception as e:
            logger.warning(f"[MEMORIA] Falha na normalizacao: {str(e)}")
            return tempo_str

    return tempo_str


def _get_memory_timestamp_from_negociacao(telefone: str) -> Optional[str]:
    """
    Busca tempo_memoria mais recente da tabela negociacao

    Input: telefone (str) - Numero do telefone
    Output: (Optional[str]) - Tempo de memoria ou None se nao encontrado
    """
    if not telefone:
        logger.warning("[MEMORIA] Busca ignorada - telefone vazio")
        return None

    try:
        table = dynamodb.Table(NEGOCIACAO_TABLE)

        response = table.query(
            KeyConditionExpression=Key('telefone').eq(telefone),
            ScanIndexForward=False,
            Limit=1,
            ProjectionExpression='tempo_memoria'
        )

        items = response.get("Items", [])

        if items and 'tempo_memoria' in items[0]:
            tempo_memoria_raw = str(items[0]['tempo_memoria'])
            tempo_memoria = _normalize_tempo_memoria_to_iso(tempo_memoria_raw)
            logger.info(f"[MEMORIA] Memoria encontrada para telefone {telefone}")
            return tempo_memoria
        else:
            logger.info(f"[MEMORIA] Nenhuma memoria encontrada para telefone {telefone}")
            return None

    except Exception as e:
        logger.error(f"[ERRO] Falha ao buscar memoria: {type(e).__name__} - {str(e)}")
        return None


def _is_memory_valid(tempo_memoria_str: str) -> bool:
    """
    Verifica se a memoria ainda e valida (menos de 7 dias)

    Input: tempo_memoria_str (str) - Timestamp da memoria
    Output: (bool) - True se memoria valida, False caso contrario
    """
    try:
        memory_timestamp = _parse_timestamp_to_unix(tempo_memoria_str)
        current_timestamp = int(dt.datetime.now(dt.timezone.utc).timestamp())

        diff_seconds = current_timestamp - memory_timestamp

        MEMORY_TIMEOUT = 604800
        is_valid = diff_seconds < MEMORY_TIMEOUT

        logger.info(f"[MEMORIA] Validacao - Diferenca: {diff_seconds}s - Valida: {is_valid}")

        return is_valid
    except Exception as e:
        logger.error(f"[ERRO] Falha na validacao de memoria: {type(e).__name__}")
        return False


def _create_new_memory_id(telefone: str) -> tuple[str, str]:
    """
    Cria novo memory_id no formato telefone_mem_timestamp

    Input: telefone (str) - Numero do telefone
    Output: (tuple) - (memory_id, tempo_memoria)
    """
    now = dt.datetime.now(dt.timezone.utc)
    tempo_memoria = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    memory_id = f"{telefone}_mem_{tempo_memoria}"

    logger.info(f"[MEMORIA] Nova memoria criada: {memory_id}")

    return memory_id, tempo_memoria


def _save_memory_timestamp_to_negociacao(telefone: str, tempo_memoria: str) -> bool:
    """
    Salva tempo_memoria na tabela negociacao

    Input: telefone (str), tempo_memoria (str)
    Output: (bool) - True se salvo com sucesso, False caso contrario
    """
    if not telefone or not tempo_memoria:
        logger.warning("[MEMORIA] Salvamento ignorado - parametros invalidos")
        return False

    try:
        table = dynamodb.Table(NEGOCIACAO_TABLE)

        tempo_sessao_recente, session_id_recente = _get_session_timestamp_from_negociacao(telefone)

        if not tempo_sessao_recente or not session_id_recente:
            logger.warning("[MEMORIA] Criando nova sessao para salvar memoria")
            session_id_recente, tempo_sessao_recente, negociacao_iniciada_em = _create_new_session_id(telefone)
            table.put_item(
                Item={
                    "telefone": telefone,
                    "tempo_sessao": tempo_sessao_recente,
                    "session_id": session_id_recente,
                    "tempo_memoria": tempo_memoria,
                    "negociacao_iniciada_em": negociacao_iniciada_em
                }
            )
        else:
            table.update_item(
                Key={
                    "telefone": telefone,
                    "tempo_sessao": tempo_sessao_recente
                },
                UpdateExpression="SET tempo_memoria = :tm",
                ExpressionAttributeValues={
                    ":tm": tempo_memoria
                }
            )

        logger.info(f"[MEMORIA] Memoria salva com sucesso para telefone {telefone}")

        return True
    except Exception as e:
        logger.error(f"[ERRO] Falha ao salvar memoria: {type(e).__name__} - {str(e)}")
        return False


def _get_or_create_memory_id(telefone: str) -> str:
    """
    Gerencia memoria: busca memoria existente ou cria nova se expirada

    Input: telefone (str) - Numero do telefone
    Output: (str) - Memory ID valido
    """
    if not telefone:
        logger.warning("[MEMORIA] Gerenciamento ignorado - telefone vazio")
        return str(uuid.uuid4())

    logger.info(f"[MEMORIA] Iniciando gerenciamento de memoria para {telefone}")

    existing_tempo_memoria = _get_memory_timestamp_from_negociacao(telefone)

    if not existing_tempo_memoria:
        logger.info("[MEMORIA] Criando nova memoria - nenhuma memoria existente")
        memory_id, tempo_memoria = _create_new_memory_id(telefone)
        _save_memory_timestamp_to_negociacao(telefone, tempo_memoria)
        return memory_id

    if _is_memory_valid(existing_tempo_memoria):
        memory_id = f"{telefone}_mem_{existing_tempo_memoria}"
        current_timestamp = int(dt.datetime.now(dt.timezone.utc).timestamp())
        memory_timestamp = _parse_timestamp_to_unix(existing_tempo_memoria)
        age_days = (current_timestamp - memory_timestamp) / 86400
        logger.info(f"[MEMORIA] Reusando memoria existente - Idade: {age_days:.1f} dias")
        return memory_id

    current_timestamp = int(dt.datetime.now(dt.timezone.utc).timestamp())
    memory_timestamp = _parse_timestamp_to_unix(existing_tempo_memoria)
    age_days = (current_timestamp - memory_timestamp) / 86400
    logger.info(f"[MEMORIA] Criando nova memoria - memoria expirada ({age_days:.1f} dias)")
    memory_id, tempo_memoria = _create_new_memory_id(telefone)
    _save_memory_timestamp_to_negociacao(telefone, tempo_memoria)
    return memory_id


def _consume_stream_and_collect_text(response) -> str:
    """
    Le o stream do invoke_agent e concatena os bytes de chunk

    Input: response (dict) - Resposta do invoke_agent
    Output: (str) - Texto completo concatenado
    """
    collected = []
    completion = response.get("completion")
    if completion is None:
        logger.warning("[AGENT] Stream sem completion")
        return ""

    event_count = 0
    chunk_count = 0
    for event in completion:
        event_count += 1
        chunk = event.get("chunk")
        if chunk and "bytes" in chunk:
            try:
                text_piece = chunk["bytes"].decode("utf-8")
            except Exception:
                text_piece = str(chunk["bytes"])
            collected.append(text_piece)
            chunk_count += 1

    final_text = "".join(collected).strip()
    logger.info(f"[AGENT] Stream processado - {chunk_count} chunks, {len(final_text)} caracteres")

    return final_text


def _invoke_agent(
    *,
    agent_id: str,
    agent_alias_id: str,
    session_id: str,
    input_text: str,
    session_attributes: Optional[Dict[str, str]] = None) -> str:
    """
    Invoca o agente Bedrock com os parametros fornecidos

    Input: agent_id (str), agent_alias_id (str), session_id (str), input_text (str), session_attributes (dict)
    Output: (str) - Resposta do agente
    """
    kwargs = {
        "agentId": agent_id,
        "agentAliasId": agent_alias_id,
        "sessionId": session_id,
        "inputText": input_text or "",
        "enableTrace": False
    }

    if session_attributes:
        kwargs["sessionState"] = {
            "sessionAttributes": session_attributes
        }

    logger.info(f"[AGENT] Invocando agente - Session: {session_id}, Atributos: {len(session_attributes) if session_attributes else 0}")

    resp = bedrock.invoke_agent(**kwargs)
    result_text = _consume_stream_and_collect_text(resp)

    logger.info(f"[AGENT] Resposta recebida - {len(result_text)} caracteres")

    return result_text


def lambda_handler(event, context):
    """
    Handler principal do Lambda - gerencia sessoes e invoca agente Bedrock

    Input: event (dict) - Evento do Lambda contendo mensagem e session attributes
    Output: (dict) - Resposta com status e texto do agente
    """
    logger.info(f"[HANDLER] Event: {json.dumps(event, ensure_ascii=False)}")
    logger.info("[HANDLER] Iniciando processamento do evento")

    try:
        mensagem = event.get("mensagem", {})
        text = mensagem.get("texto", "")

        session_attributes = event.get("sessionAttributes", {})
        session_attributes_str = {k: _safe_str(v) for k, v in session_attributes.items()}

        logger.info(f"[HANDLER] Atributos recebidos: {len(session_attributes_str)}")

        telefone_raw = session_attributes_str.get("telefone", "") or session_attributes_str.get("motorista_telefone", "")
        telefone = normalizar_telefone(telefone_raw) if telefone_raw else ""

        if telefone:
            session_id = _get_or_create_session_id(telefone)
            memory_id = _get_or_create_memory_id(telefone)
        else:
            session_id = _safe_str(mensagem.get("codConversa", ""))
            if not session_id:
                session_id = str(uuid.uuid4())
                logger.warning("[HANDLER] Usando UUID para sessao - telefone nao fornecido")
            memory_id = str(uuid.uuid4())

        session_attributes_str['session_id'] = session_id
        session_attributes_str['memory_id'] = memory_id

        logger.info(f"[HANDLER] Sessao: {session_id}, Memoria: {memory_id}")

        agent_response_text = _invoke_agent(
            agent_id=AGENT_ID,
            agent_alias_id=AGENT_ALIAS,
            session_id=session_id,
            input_text=text,
            session_attributes=session_attributes_str
        )

        logger.info(f"[HANDLER] Processamento concluido com sucesso - {len(agent_response_text)} caracteres")

        return {
            "status": "success",
            "resposta": agent_response_text
        }

    except Exception as e:
        logger.error(f"[ERRO] Excecao no handler: {type(e).__name__} - {str(e)}")
        import traceback
        traceback.print_exc()

        return {
            "status": "error",
            "message": f"{type(e).__name__}: {e}"
        }
