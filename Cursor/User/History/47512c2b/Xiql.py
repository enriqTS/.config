"""
Funcao Lambda de verificacao de cadastro de motorista na API Rodosafra

Input: CPF (opcional) e/ou telefone (opcional) - pelo menos um deve ser fornecido
Output: Status da verificacao, dados do motorista, veiculos e equipamentos se encontrado
"""

import json
import os
import logging
import requests
import traceback
import sys
from typing import Dict, Any, Tuple, Optional
import boto3
from botocore.exceptions import ClientError
from decimal import Decimal
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../chatbot/action-groups'))
from api_error_logger import log_api_error
from api_retry_util import retry_on_timeout

logger = logging.getLogger()
logger.setLevel(logging.INFO)

API_BASE_URL = os.environ.get(
    "RODOSAFRA_API_BASE_URL", "https://api-staging.rodosafra.net/api"
)

PARAMETER_STORE_TOKEN_NAME = os.environ.get(
    "PARAMETER_STORE_TOKEN_NAME", "/rodosafra/auth/token"
)

REQUEST_TIMEOUT = 15

ssm_client = boto3.client("ssm")
dynamodb = boto3.resource("dynamodb")

MOTORISTAS_TABLE = os.environ.get("MOTORISTAS_TABLE_NAME", "motoristas")
VEICULOS_TABLE = os.environ.get("VEICULOS_TABLE_NAME", "veiculos")
EQUIPAMENTOS_TABLE = os.environ.get("EQUIPAMENTOS_TABLE_NAME", "equipamentos")

auth_cookie_cache = None

def normalizar_telefone(telefone: str) -> str:
    """
    Normaliza numero de telefone para formato padrao com codigo do pais 55

    Input: telefone (str) - Numero em qualquer formato
    Output: (str) - Telefone normalizado com 13 digitos (55 + DDD + numero)
    """
    if not telefone:
        logger.warning("[VALIDACAO] Telefone vazio recebido para normalizacao")
        return telefone

    telefone_limpo = ''.join(filter(str.isdigit, str(telefone)))

    logger.info(f"[VALIDACAO] Normalizando telefone: '{telefone}' -> '{telefone_limpo}' ({len(telefone_limpo)} digitos)")

    if telefone_limpo.startswith('55') and len(telefone_limpo) == 13:
        logger.info(f"[VALIDACAO] Telefone ja normalizado: {telefone_limpo}")
        return telefone_limpo

    if len(telefone_limpo) == 11:
        telefone_normalizado = f"55{telefone_limpo}"
        logger.info(f"[VALIDACAO] Telefone normalizado de 11 para 13 digitos: {telefone_normalizado}")
        return telefone_normalizado

    if len(telefone_limpo) == 13 and not telefone_limpo.startswith('55'):
        logger.warning(f"[VALIDACAO] Telefone com 13 digitos mas nao comeca com 55: {telefone_limpo}")
        return telefone_limpo

    if len(telefone_limpo) > 13:
        logger.warning(f"[VALIDACAO] Telefone com mais de 13 digitos: {telefone_limpo} - removendo excesso")
        if telefone_limpo.endswith(telefone_limpo[-13:]) and telefone_limpo[-13:-11] == '55':
            return telefone_limpo[-13:]
        return telefone_limpo

    if len(telefone_limpo) < 11:
        logger.error(f"[ERRO] Telefone com menos de 11 digitos apos limpeza: {telefone_limpo} (original: {telefone})")
        return telefone_limpo

    logger.warning(f"[VALIDACAO] Telefone nao se encaixa em nenhum padrao conhecido: {telefone_limpo}")
    return telefone_limpo

def obter_token_do_parameter_store() -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Obtem token de autenticacao do AWS Parameter Store

    Input: Nenhum (usa variavel de ambiente PARAMETER_STORE_TOKEN_NAME)
    Output: (bool, str, str) - Sucesso, cookie de auth, mensagem de erro se houver
    """
    global auth_cookie_cache

    if auth_cookie_cache:
        logger.info("[AUTH] Usando token em cache")
        return True, auth_cookie_cache, None

    logger.info(f"[AUTH] Buscando token no Parameter Store: {PARAMETER_STORE_TOKEN_NAME}")

    try:
        response = ssm_client.get_parameter(
            Name=PARAMETER_STORE_TOKEN_NAME, WithDecryption=True
        )

        token = response["Parameter"]["Value"]

        if not token:
            return False, None, "Token vazio no Parameter Store"

        auth_cookie_cache = token

        logger.info("[AUTH] Token obtido com sucesso do Parameter Store")
        return True, token, None

    except ClientError as e:
        error_code = e.response["Error"]["Code"]

        if error_code == "ParameterNotFound":
            logger.error(
                f"[ERRO] Token nao encontrado no Parameter Store: {PARAMETER_STORE_TOKEN_NAME}"
            )
            return False, None, "Token nao encontrado no Parameter Store"

        elif error_code == "AccessDeniedException":
            logger.error("[ERRO] Sem permissao para acessar Parameter Store")
            return False, None, "Sem permissao para acessar token"

        else:
            logger.error(f"[ERRO] Erro ao acessar Parameter Store: {error_code}")
            return False, None, f"Erro ao obter token: {error_code}"

    except Exception as e:
        logger.error(f"[ERRO] Erro inesperado ao obter token: {str(e)}", exc_info=True)
        return False, None, f"Erro inesperado: {str(e)}"

def autenticar_api_fallback() -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Autenticacao direta na API como fallback caso Parameter Store falhe

    Input: Credenciais de ambiente (RODOSAFRA_API_USERNAME, RODOSAFRA_API_PASSWORD)
    Output: (bool, str, str) - Sucesso, cookie de auth, mensagem de erro se houver
    """
    logger.warning("[AUTH] Usando autenticacao fallback - isto nao deveria acontecer")

    API_USERNAME = os.environ.get("RODOSAFRA_API_USERNAME")
    API_PASSWORD = os.environ.get("RODOSAFRA_API_PASSWORD")

    if not API_USERNAME or not API_PASSWORD:
        return False, None, "Credenciais de fallback nao configuradas"

    try:
        url = f"{API_BASE_URL}/publico/login"
        payload = {"usuario": str(API_USERNAME), "senha": str(API_PASSWORD)}

        logger.info(f"[API] Payload para {url}: {json.dumps(payload, ensure_ascii=False)}")

        response = retry_on_timeout(
            lambda: requests.post(url, json=payload, timeout=REQUEST_TIMEOUT),
            max_retries=3,
            operation_name="Autenticacao fallback"
        )

        if response.status_code == 200:
            data = response.json()
            cookie_name = data.get("nomeCookie")
            token = data.get("token")

            if not cookie_name or not token:
                return False, None, "Resposta de autenticacao invalida"

            auth_cookie = f"{cookie_name}={token}"
            logger.info("[AUTH] Autenticacao fallback realizada com sucesso")
            return True, auth_cookie, None

        elif response.status_code == 401:
            return False, None, "Usuario ou senha invalidos"

        else:
            return False, None, f"Erro na autenticacao: HTTP {response.status_code}"

    except Exception as e:
        return False, None, f"Erro no fallback: {str(e)}"

def obter_token_autenticacao(
    permitir_fallback: bool = False,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Obtem token de autenticacao do Parameter Store com opcao de fallback

    Input: permitir_fallback (bool) - Se True tenta autenticacao direta em caso de falha
    Output: (bool, str, str) - Sucesso, cookie de auth, mensagem de erro se houver
    """
    sucesso, token, erro = obter_token_do_parameter_store()

    if sucesso:
        return True, token, None

    if permitir_fallback:
        logger.warning("[AUTH] Tentando fallback de autenticacao")
        return autenticar_api_fallback()

    return False, None, erro

def atualizar_flag_cadastrado(
    id_motorista: int, cadastrado: bool
) -> Tuple[bool, Optional[str]]:
    """
    Atualiza flag de cadastrado na tabela de motoristas do DynamoDB

    Input: id_motorista (int), cadastrado (bool) - True se encontrado False se nao
    Output: (bool, str) - Sucesso e mensagem de erro se houver
    """
    try:
        table = dynamodb.Table(MOTORISTAS_TABLE)

        logger.info(
            f"[DYNAMODB] Atualizando flag cadastrado para motorista {id_motorista}: {cadastrado}"
        )

        try:
            response = table.get_item(Key={"id_motorista": id_motorista})
            item_existe = "Item" in response
            logger.info(f"[DYNAMODB] Item existe na tabela: {item_existe}")

        except Exception as e:
            logger.warning(f"[DYNAMODB] Erro ao verificar existencia do item: {str(e)}")
            item_existe = False

        if not item_existe:
            logger.info(
                f"[DYNAMODB] Item nao existe, criando registro minimo para motorista {id_motorista}"
            )

            item = {
                "id_motorista": id_motorista,
                "cadastrado": cadastrado,
                "updated_at": datetime.utcnow().isoformat() + "Z",
                "created_at": datetime.utcnow().isoformat() + "Z",
            }

            table.put_item(Item=item)
            logger.info(f"[DYNAMODB] Registro criado com cadastrado = {cadastrado}")

        else:
            response = table.update_item(
                Key={"id_motorista": id_motorista},
                UpdateExpression="SET cadastrado = :cadastrado, updated_at = :updated_at",
                ExpressionAttributeValues={
                    ":cadastrado": cadastrado,
                    ":updated_at": datetime.utcnow().isoformat() + "Z",
                },
                ReturnValues="ALL_NEW",
            )

            logger.info(
                f"[DYNAMODB] Flag cadastrado atualizado com sucesso - ID: {id_motorista}, Cadastrado: {cadastrado}"
            )

        return True, None

    except ClientError as e:
        error_msg = f"Erro ao atualizar flag cadastrado: {e.response['Error']['Code']}"
        logger.error(f"[ERRO] {error_msg}")
        return False, error_msg

    except Exception as e:
        error_msg = f"Erro inesperado ao atualizar flag cadastrado: {str(e)}"
        logger.error(f"[ERRO] {error_msg}", exc_info=True)
        return False, error_msg

def verificar_cadastro_dual(
    cpf: Optional[str] = None,
    telefone: Optional[str] = None,
    id_motorista: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Verifica cadastro com duas verificacoes separadas por telefone e CPF

    Input: cpf (str opcional), telefone (str opcional), id_motorista (int opcional)
    Output: (dict) - Status, flags cadastrado_telefone e cadastrado_cpf, session_attributes
    """
    logger.info("[VERIFICACAO] Iniciando verificacao dual")
    logger.info(f"[VERIFICACAO] CPF: {cpf if cpf else 'Nao fornecido'}")
    logger.info(f"[VERIFICACAO] Telefone: {telefone if telefone else 'Nao fornecido'}")
    logger.info(f"[VERIFICACAO] ID Motorista: {id_motorista if id_motorista else 'Nao fornecido'}")

    if not cpf and not telefone:
        return {
            "status": "erro",
            "cadastrado_telefone": False,
            "cadastrado_cpf": False,
            "session_attributes": {},
            "mensagem": "Informe CPF ou telefone",
            "tipo_erro": "validacao",
        }

    cadastrado_telefone = False
    cadastrado_cpf = False
    session_attributes = {}
    motorista_data = None

    if telefone:
        logger.info("[VERIFICACAO] Verificacao 1: Apenas telefone")
        resultado_telefone = verificar_cadastro_motorista(
            telefone=telefone, salvar_no_db=False
        )

        if resultado_telefone["status"] == "encontrado":
            cadastrado_telefone = True
            motorista_data = resultado_telefone.get("motorista", {})
            logger.info(
                f"[VERIFICACAO] Telefone encontrado - Motorista: {motorista_data.get('nomeCompleto')}"
            )

            session_attributes = extrair_session_attributes(resultado_telefone)

        elif resultado_telefone["status"] == "nao_encontrado":
            cadastrado_telefone = False
            logger.info("[VERIFICACAO] Telefone nao encontrado")
        else:
            logger.error(
                f"[ERRO] Erro ao verificar telefone: {resultado_telefone.get('mensagem')}"
            )

    if cpf:
        logger.info("[VERIFICACAO] Verificacao 2: Apenas CPF")
        resultado_cpf = verificar_cadastro_motorista(cpf=cpf, salvar_no_db=False)

        if resultado_cpf["status"] == "encontrado":
            cadastrado_cpf = True
            motorista_data_cpf = resultado_cpf.get("motorista", {})
            logger.info(
                f"[VERIFICACAO] CPF encontrado - Motorista: {motorista_data_cpf.get('nomeCompleto')}"
            )

            if not session_attributes:
                session_attributes = extrair_session_attributes(resultado_cpf)
                motorista_data = motorista_data_cpf

        elif resultado_cpf["status"] == "nao_encontrado":
            cadastrado_cpf = False
            logger.info("[VERIFICACAO] CPF nao encontrado")
        else:
            logger.error(f"[ERRO] Erro ao verificar CPF: {resultado_cpf.get('mensagem')}")

    if cadastrado_telefone and cadastrado_cpf:
        status = "encontrado"
        mensagem = (
            "CENARIO 1: Telefone E CPF cadastrados - Confirmar identidade e prosseguir"
        )
        cadastrado_geral = True

    elif cadastrado_telefone and not cadastrado_cpf:
        status = "parcial"
        mensagem = (
            "CENARIO 2A: Telefone cadastrado mas CPF diferente - Conflito de identidade"
        )
        cadastrado_geral = True

    elif not cadastrado_telefone and cadastrado_cpf:
        status = "parcial"
        mensagem = (
            "CENARIO 2B: CPF cadastrado mas telefone diferente - Atualizar telefone"
        )
        cadastrado_geral = True

    else:
        status = "nao_encontrado"
        mensagem = "CENARIO 3: Nem telefone nem CPF cadastrados - Cadastro novo"
        cadastrado_geral = False

    flag_atualizado = False
    if id_motorista:
        sucesso_flag, erro_flag = atualizar_flag_cadastrado(
            id_motorista, cadastrado_geral
        )
        flag_atualizado = sucesso_flag
        if not sucesso_flag:
            logger.error(f"[ERRO] Erro ao atualizar flag cadastrado: {erro_flag}")

    salvamento = None
    if motorista_data and status in ["encontrado", "parcial"]:
        logger.info("[DYNAMODB] Salvando dados completos no DynamoDB")

        veiculos_data = {}
        if "veiculos" in session_attributes:
            veiculos_data = session_attributes["veiculos"]

        veiculo_cavalo = veiculos_data.get("cavalo")
        equipamentos = []
        for i in range(1, 4):
            equip = veiculos_data.get(f"equipamento{i}")
            if equip:
                equipamentos.append(equip)

        salvamento = salvar_dados_completos(
            motorista_data, veiculo_cavalo, equipamentos
        )
        logger.info(f"[DYNAMODB] Salvamento concluido: {salvamento}")

    logger.info("[VERIFICACAO] Resultado verificacao dual")
    logger.info(f"[VERIFICACAO] Status: {status}")
    logger.info(f"[VERIFICACAO] Telefone cadastrado: {cadastrado_telefone}")
    logger.info(f"[VERIFICACAO] CPF cadastrado: {cadastrado_cpf}")
    logger.info(f"[VERIFICACAO] Mensagem: {mensagem}")
    logger.info(f"[VERIFICACAO] Session attributes: {len(session_attributes)} campos")
    logger.info(f"[VERIFICACAO] Flag atualizado: {flag_atualizado}")

    resultado = {
        "status": status,
        "cadastrado_telefone": cadastrado_telefone,
        "cadastrado_cpf": cadastrado_cpf,
        "cadastrado": cadastrado_geral,
        "session_attributes": session_attributes,
        "mensagem": mensagem,
        "flag_cadastrado_atualizado": flag_atualizado,
    }

    if motorista_data:
        resultado["motorista"] = motorista_data
        resultado["resumo"] = {
            "id": motorista_data.get("id"),
            "nome": motorista_data.get("nomeCompleto"),
            "cpf": motorista_data.get("cpf"),
            "telefone": motorista_data.get("telefone"),
        }

    if salvamento:
        resultado["salvamento"] = salvamento

    return resultado

def extrair_session_attributes(resultado_verificacao: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extrai e formata session attributes do resultado da verificacao de motorista

    Input: resultado_verificacao (dict) - Resultado da funcao verificar_cadastro_motorista
    Output: (dict) - Session attributes formatados para uso na sessao
    """
    session_attributes = {}
    
    if resultado_verificacao.get('status') != 'encontrado':
        return session_attributes
    
    # Dados do motorista
    motorista = resultado_verificacao.get('motorista', {})
    if motorista:
        session_attributes['motorista_id'] = str(motorista.get('id', ''))
        session_attributes['motorista_nome'] = motorista.get('nomeCompleto', '')
        session_attributes['motorista_cpf'] = motorista.get('cpf', '')
        session_attributes['motorista_telefone'] = motorista.get('telefone', '')
        session_attributes['motorista_cnh'] = motorista.get('categoriaCnh', '')
        session_attributes['motorista_validade_cnh'] = motorista.get('validadeCnh', '')
        session_attributes['motorista_data_nascimento'] = motorista.get('dataNascimento', '')
        session_attributes['motorista_status'] = motorista.get('statusCadastro', '')
    
    # Dados dos veiculos
    veiculos = resultado_verificacao.get('veiculos', {})
    if veiculos:
        session_attributes['veiculos'] = veiculos
        session_attributes['total_veiculos'] = veiculos.get('total', 0)
        
        # Veiculo principal
        cavalo = veiculos.get('cavalo')
        if cavalo:
            session_attributes['veiculo_placa'] = cavalo.get('placa', '')
            session_attributes['veiculo_tipo'] = cavalo.get('tipoVeiculoNome', '')
            session_attributes['veiculo_id'] = str(cavalo.get('id', ''))
    
    # Embarque ativo
    session_attributes['tem_embarque_ativo'] = resultado_verificacao.get('tem_embarque', False)
    if resultado_verificacao.get('embarque_ativo'):
        session_attributes['embarque_ativo'] = resultado_verificacao['embarque_ativo']

    # Data/hora atual para contexto temporal
    from datetime import datetime, timezone
    session_attributes['data_atual'] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    return session_attributes

def verificar_cadastro_motorista(
    cpf: Optional[str] = None,
    telefone: Optional[str] = None,
    id_motorista: Optional[int] = None,
    salvar_no_db: bool = True,
) -> Dict[str, Any]:
    """
    Verifica se motorista possui cadastro na API e opcionalmente salva no DynamoDB

    Input: cpf (str opcional), telefone (str opcional), id_motorista (int opcional), salvar_no_db (bool)
    Output: (dict) - Status, dados do motorista, veiculos e flag de cadastrado atualizado
    """
    logger.info("[VERIFICACAO] Iniciando verificacao de cadastro")
    logger.info(f"[VERIFICACAO] CPF: {cpf if cpf else 'Nao fornecido'}")
    logger.info(f"[VERIFICACAO] Telefone: {telefone if telefone else 'Nao fornecido'}")
    logger.info(f"[VERIFICACAO] ID Motorista: {id_motorista if id_motorista else 'Nao fornecido'}")
    logger.info(f"[VERIFICACAO] Salvar no DB: {salvar_no_db}")

    if not cpf and not telefone:
        return {
            "status": "erro",
            "cadastrado": None,
            "tipo_erro": "validacao",
            "mensagem": "Informe CPF ou telefone",
        }

    autenticado, cookie, erro_auth = obter_token_autenticacao(permitir_fallback=False)

    if not autenticado:
        logger.error(f"[ERRO] Falha ao obter token: {erro_auth}")
        return {
            "status": "erro",
            "cadastrado": None,
            "tipo_erro": "autenticacao",
            "mensagem": erro_auth,
        }

    try:
        url = f"{API_BASE_URL}/publico/motorista/v1/verificar-cadastro"

        params = {}

        if cpf:
            params["cpf"] = cpf

        if telefone:
            params["telefone"] = telefone

        headers = {"Cookie": cookie}

        logger.info(f"[API] URL: {url}")
        logger.info(f"[API] Params: {params}")
        logger.info(f"[API] Requisição GET para {url} com params: {json.dumps(params, ensure_ascii=False)}")

        response = retry_on_timeout(
            lambda: requests.get(
                url, params=params, headers=headers, timeout=REQUEST_TIMEOUT
            ),
            max_retries=3,
            operation_name="Verificar cadastro motorista"
        )

        logger.info(f"[API] Status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            motorista = data.get("motorista", {})
            veiculo_cavalo = data.get("veiculoCavaloOuCaminhao")
            veiculo_equip1 = data.get("veiculoEquipamento1")
            veiculo_equip2 = data.get("veiculoEquipamento2")
            veiculo_equip3 = data.get("veiculoEquipamento3")
            embarque = data.get("embarque")

            veiculos = []

            if veiculo_cavalo:
                veiculos.append(veiculo_cavalo)

            if veiculo_equip1:
                veiculos.append(veiculo_equip1)

            if veiculo_equip2:
                veiculos.append(veiculo_equip2)

            if veiculo_equip3:
                veiculos.append(veiculo_equip3)

            logger.info(f"[VERIFICACAO] Motorista encontrado")
            logger.info(f"[VERIFICACAO] ID: {motorista.get('id')}")
            logger.info(f"[VERIFICACAO] Nome: {motorista.get('nomeCompleto')}")
            logger.info(f"[VERIFICACAO] CPF: {motorista.get('cpf')}")
            logger.info(f"[VERIFICACAO] Veiculos: {len(veiculos)}")
            logger.info(f"[VERIFICACAO] Tem embarque: {'Sim' if embarque else 'Nao'}")

            flag_atualizado = False

            if id_motorista:
                sucesso_flag, erro_flag = atualizar_flag_cadastrado(id_motorista, True)
                flag_atualizado = sucesso_flag

                if not sucesso_flag:
                    logger.error(f"[ERRO] Erro ao atualizar flag cadastrado: {erro_flag}")

            resultado_salvamento = None

            if salvar_no_db:
                logger.info("[DYNAMODB] Salvando dados no DynamoDB")

                equipamentos = [
                    eq for eq in [veiculo_equip1, veiculo_equip2, veiculo_equip3] if eq
                ]

                resultado_salvamento = salvar_dados_completos(
                    motorista, veiculo_cavalo, equipamentos
                )

                logger.info(
                    f"[DYNAMODB] Motorista salvo: {resultado_salvamento['motorista_salvo']}"
                )
                logger.info(
                    f"[DYNAMODB] Veiculo salvo: {resultado_salvamento['veiculo_salvo']}"
                )
                logger.info(
                    f"[DYNAMODB] Equipamentos salvos: {resultado_salvamento['equipamentos_salvos']}"
                )

                if resultado_salvamento["erros"]:
                    logger.warning(
                        f"[DYNAMODB] Erros no salvamento: {resultado_salvamento['erros']}"
                    )

            return {
                "status": "encontrado",
                "cadastrado": True,
                "motorista": motorista,
                "veiculos": {
                    "cavalo": veiculo_cavalo,
                    "equipamento1": veiculo_equip1,
                    "equipamento2": veiculo_equip2,
                    "equipamento3": veiculo_equip3,
                    "total": len(veiculos),
                },
                "embarque_ativo": embarque,
                "tem_embarque": bool(embarque),
                "mensagem": f"Motorista {motorista.get('nomeCompleto')} encontrado",
                "salvamento": resultado_salvamento,
                "flag_cadastrado_atualizado": flag_atualizado,
                "resumo": {
                    "id": motorista.get("id"),
                    "nome": motorista.get("nomeCompleto"),
                    "cpf": motorista.get("cpf"),
                    "telefone": motorista.get("telefone"),
                    "categoria_cnh": motorista.get("categoriaCnh"),
                    "total_veiculos": len(veiculos),
                    "tem_embarque_ativo": bool(embarque),
                    "dados_salvos": resultado_salvamento is not None,
                },
            }

        elif response.status_code == 401:
            logger.error("[ERRO] Token invalido ou expirado")

            return {
                "status": "erro",
                "cadastrado": None,
                "tipo_erro": "token_invalido",
                "mensagem": "Token de autenticacao invalido ou expirado",
                "detalhes": "O token no Parameter Store pode estar desatualizado",
            }

        elif response.status_code == 404:
            logger.info("[VERIFICACAO] Motorista nao encontrado")

            flag_atualizado = False

            if id_motorista:
                sucesso_flag, erro_flag = atualizar_flag_cadastrado(id_motorista, False)
                flag_atualizado = sucesso_flag

                if not sucesso_flag:
                    logger.error(f"[ERRO] Erro ao atualizar flag cadastrado: {erro_flag}")

            return {
                "status": "nao_encontrado",
                "cadastrado": False,
                "mensagem": "Motorista nao possui cadastro",
                "pode_cadastrar": True,
                "flag_cadastrado_atualizado": flag_atualizado,
                "dados_busca": {"cpf": cpf, "telefone": telefone},
            }

        elif response.status_code == 500:
            logger.error("[ERRO] Erro interno no servidor")

            log_api_error(
                api_route="/publico/motorista/v1/verificar-cadastro",
                error_code=500,
                error_message="Erro interno no servidor ao verificar motorista",
                payload={"cpf": "***" if cpf else None, "telefone": f"***{telefone[-4:]}" if telefone else None},
                response_body=response.text
            )

            return {
                "status": "erro",
                "cadastrado": None,
                "tipo_erro": "servidor",
                "mensagem": "Erro interno no servidor",
                "detalhes": "Tente novamente em alguns instantes",
            }

        else:
            logger.error(f"[ERRO] Erro HTTP inesperado: {response.status_code}")

            if response.status_code >= 500:
                log_api_error(
                    api_route="/publico/motorista/v1/verificar-cadastro",
                    error_code=response.status_code,
                    error_message=f"Erro HTTP inesperado ao verificar motorista ({response.status_code})",
                    payload={"cpf": "***" if cpf else None, "telefone": f"***{telefone[-4:]}" if telefone else None},
                    response_body=response.text
                )

            return {
                "status": "erro",
                "cadastrado": None,
                "tipo_erro": "http",
                "mensagem": f"Erro ao verificar cadastro: HTTP {response.status_code}",
                "detalhes": response.text[:200],
            }

    except requests.exceptions.Timeout:
        logger.error("[ERRO] Timeout na requisicao")

        return {
            "status": "erro",
            "cadastrado": None,
            "tipo_erro": "timeout",
            "mensagem": "Timeout ao verificar cadastro",
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"[ERRO] Erro de conexao: {str(e)}")

        return {
            "status": "erro",
            "cadastrado": None,
            "tipo_erro": "conexao",
            "mensagem": "Erro de conexao com a API",
            "detalhes": str(e),
        }

    except Exception as e:
        logger.error(f"[ERRO] Erro inesperado: {str(e)}", exc_info=True)

        return {
            "status": "erro",
            "cadastrado": None,
            "tipo_erro": "inesperado",
            "mensagem": "Erro inesperado ao verificar cadastro",
            "detalhes": str(e),
        }

def converter_para_decimal(obj):
    """
    Converte numeros para tipo Decimal para compatibilidade com DynamoDB

    Input: obj (any) - Objeto a ser convertido (dict, list, float, int ou outro)
    Output: (any) - Objeto com numeros convertidos para Decimal
    """
    if isinstance(obj, dict):
        return {k: converter_para_decimal(v) for k, v in obj.items()}

    elif isinstance(obj, list):
        return [converter_para_decimal(item) for item in obj]

    elif isinstance(obj, float):
        return Decimal(str(obj))

    elif isinstance(obj, int):
        return Decimal(obj)

    return obj

def salvar_motorista(motorista_data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Salva dados do motorista na tabela motoristas do DynamoDB

    Input: motorista_data (dict) - Dados do motorista retornados pela API
    Output: (bool, str) - Sucesso e mensagem de erro se houver
    """
    try:
        table = dynamodb.Table(MOTORISTAS_TABLE)

        item = {
            "id_motorista": motorista_data.get("id"),
            "nome_completo": motorista_data.get("nomeCompleto"),
            "cpf": motorista_data.get("cpf"),
            "categoria_cnh": motorista_data.get("categoriaCnh"),
            "validade_cnh": motorista_data.get("validadeCnh"),
            "data_nascimento": motorista_data.get("dataNascimento"),
            "telefone": motorista_data.get("telefone"),
            "status_cadastro": motorista_data.get("statusCadastro"),
        }

        item = {k: v for k, v in item.items() if v is not None}

        item = converter_para_decimal(item)

        table.put_item(Item=item)

        logger.info(f"[DYNAMODB] Motorista salvo com sucesso - ID: {item['id_motorista']}")
        return True, None

    except ClientError as e:
        error_msg = f"Erro ao salvar motorista: {e.response['Error']['Code']}"
        logger.error(f"[ERRO] {error_msg}")
        return False, error_msg

    except Exception as e:
        error_msg = f"Erro inesperado ao salvar motorista: {str(e)}"
        logger.error(f"[ERRO] {error_msg}", exc_info=True)
        return False, error_msg

def salvar_veiculo(
    veiculo_data: Dict[str, Any],
    motorista_data: Dict[str, Any],
    equipamentos: list = None,
) -> Tuple[bool, Optional[str]]:
    """
    Salva dados do veiculo principal na tabela veiculos do DynamoDB

    Input: veiculo_data (dict), motorista_data (dict), equipamentos (list opcional)
    Output: (bool, str) - Sucesso e mensagem de erro se houver
    """
    try:
        table = dynamodb.Table(VEICULOS_TABLE)

        id_veiculo = str(veiculo_data.get("id"))
        id_motorista = str(motorista_data.get("id"))

        if not id_veiculo or id_veiculo == "None":
            return False, "ID do veiculo invalido"

        if not id_motorista or id_motorista == "None":
            return False, "ID do motorista invalido"

        logger.info(
            f"[DYNAMODB] Salvando veiculo {veiculo_data.get('placa')} (ID: {id_veiculo}) com motorista {id_motorista}"
        )

        timestamp = datetime.utcnow().isoformat() + "Z"

        veiculo_completo = converter_para_decimal(veiculo_data)
        motorista_completo = converter_para_decimal(motorista_data)

        response = table.get_item(
            Key={"id_veiculo": id_veiculo, "id_motorista": id_motorista}
        )

        item_existe = "Item" in response

        item = {
            "id_veiculo": id_veiculo,
            "id_motorista": id_motorista,
            "placa": veiculo_data.get("placa"),
            "renavam": veiculo_data.get("renavam"),
            "cavaloOuCaminhao": veiculo_data.get("cavaloOuCaminhao", False),
            "tipoVeiculoId": veiculo_data.get("tipoVeiculoId"),
            "tipoVeiculoNome": veiculo_data.get("tipoVeiculoNome"),
            "tipoEquipamentoId": veiculo_data.get("tipoEquipamentoId"),
            "tipoEquipamentoNome": veiculo_data.get("tipoEquipamentoNome"),
            "dataValidadeLicenciamento": veiculo_data.get("dataValidadeLicenciamento"),
            "anoExercicio": veiculo_data.get("anoExercicio"),
            "statusCadastro": veiculo_data.get("statusCadastro"),
            "veiculo_completo": veiculo_completo,
            "total_equipamentos": len(equipamentos) if equipamentos else 0,
            "updated_at": timestamp,
            "source": "verificacao_motorista",
        }

        if motorista_completo:
            item["motorista"] = motorista_completo
            item["motorista_nome"] = motorista_data.get(
                "nomeCompleto"
            ) or motorista_data.get("nome")
            item["motorista_cpf"] = motorista_data.get("cpf")
            item["motorista_telefone"] = motorista_data.get("telefone")
            item["motorista_cnh"] = motorista_data.get("categoriaCnh")

        if not item_existe:
            item["created_at"] = timestamp
            logger.info(
                f"[DYNAMODB] Criando novo registro para veiculo {veiculo_data.get('placa')}"
            )
        else:
            item["created_at"] = response["Item"].get("created_at", timestamp)
            logger.info(
                f"[DYNAMODB] Atualizando registro existente do veiculo {veiculo_data.get('placa')}"
            )

        item = {k: v for k, v in item.items() if v is not None}

        table.put_item(Item=item)

        logger.info(
            f"[DYNAMODB] Veiculo salvo com sucesso - ID: {id_veiculo}, Placa: {item.get('placa')}"
        )

        return True, None

    except ClientError as e:
        error_msg = f"Erro ao salvar veiculo: {e.response['Error']['Code']}"
        logger.error(f"[ERRO] {error_msg}")
        return False, error_msg

    except Exception as e:
        error_msg = f"Erro inesperado ao salvar veiculo: {str(e)}"
        logger.error(f"[ERRO] {error_msg}", exc_info=True)
        return False, error_msg

def salvar_equipamento(
    equipamento_data: Dict[str, Any], id_veiculo: int
) -> Tuple[bool, Optional[str]]:
    """
    Salva dados de equipamento na tabela equipamentos do DynamoDB

    Input: equipamento_data (dict), id_veiculo (int) - ID do veiculo principal
    Output: (bool, str) - Sucesso e mensagem de erro se houver
    """
    try:
        table = dynamodb.Table(EQUIPAMENTOS_TABLE)

        id_equipamento = str(equipamento_data.get("id"))
        id_veiculo_str = str(id_veiculo)

        if not id_equipamento or id_equipamento == "None":
            return False, "ID do equipamento invalido"

        logger.info(
            f"[DYNAMODB] Salvando equipamento {equipamento_data.get('placa')} (ID: {id_equipamento})"
        )

        timestamp = datetime.utcnow().isoformat() + "Z"

        equipamento_completo = converter_para_decimal(equipamento_data)

        response = table.get_item(
            Key={"id_equipamento": id_equipamento, "id_veiculo": id_veiculo_str}
        )

        item_existe = "Item" in response

        item = {
            "id_equipamento": id_equipamento,
            "id_veiculo": id_veiculo_str,
            "placa": equipamento_data.get("placa"),
            "renavam": equipamento_data.get("renavam"),
            "cavaloOuCaminhao": equipamento_data.get("cavaloOuCaminhao", False),
            "tipoVeiculoId": equipamento_data.get("tipoVeiculoId"),
            "tipoVeiculoNome": equipamento_data.get("tipoVeiculoNome"),
            "tipoEquipamentoId": equipamento_data.get("tipoEquipamentoId"),
            "tipoEquipamentoNome": equipamento_data.get("tipoEquipamentoNome"),
            "dataValidadeLicenciamento": equipamento_data.get(
                "dataValidadeLicenciamento"
            ),
            "anoExercicio": equipamento_data.get("anoExercicio"),
            "statusCadastro": equipamento_data.get("statusCadastro"),
            "equipamento_completo": equipamento_completo,
            "updated_at": timestamp,
            "source": "verificacao_motorista",
        }

        if not item_existe:
            item["created_at"] = timestamp
        else:
            item["created_at"] = response["Item"].get("created_at", timestamp)

        item = {k: v for k, v in item.items() if v is not None}

        table.put_item(Item=item)

        logger.info(
            f"[DYNAMODB] Equipamento salvo com sucesso - ID: {id_equipamento}, Placa: {item.get('placa')}"
        )

        return True, None

    except ClientError as e:
        error_msg = f"Erro ao salvar equipamento: {e.response['Error']['Code']}"
        logger.error(f"[ERRO] {error_msg}")
        return False, error_msg

    except Exception as e:
        error_msg = f"Erro inesperado ao salvar equipamento: {str(e)}"
        logger.error(f"[ERRO] {error_msg}", exc_info=True)
        return False, error_msg

def salvar_dados_completos(
    motorista_data: Dict[str, Any],
    veiculo_cavalo: Optional[Dict[str, Any]],
    equipamentos: list,
) -> Dict[str, Any]:
    """
    Salva motorista veiculo e equipamentos no DynamoDB de forma completa

    Input: motorista_data (dict), veiculo_cavalo (dict opcional), equipamentos (list)
    Output: (dict) - Resultados da operacao com flags de sucesso e erros
    """
    resultados = {
        "motorista_salvo": False,
        "veiculo_salvo": False,
        "equipamentos_salvos": 0,
        "erros": [],
    }

    if motorista_data and motorista_data.get("id"):
        sucesso, erro = salvar_motorista(motorista_data)
        resultados["motorista_salvo"] = sucesso

        if not sucesso:
            resultados["erros"].append(f"Motorista: {erro}")
    else:
        resultados["erros"].append("Dados do motorista invalidos")

    if veiculo_cavalo and veiculo_cavalo.get("id") and motorista_data.get("id"):
        sucesso, erro = salvar_veiculo(
            veiculo_cavalo, motorista_data, equipamentos
        )
        resultados["veiculo_salvo"] = sucesso

        if not sucesso:
            resultados["erros"].append(f"Veiculo: {erro}")

        if sucesso and equipamentos:
            id_veiculo = veiculo_cavalo["id"]

            for idx, equip in enumerate(equipamentos, 1):
                if equip and equip.get("id"):
                    sucesso_eq, erro_eq = salvar_equipamento(equip, id_veiculo)

                    if sucesso_eq:
                        resultados["equipamentos_salvos"] += 1
                    else:
                        resultados["erros"].append(f"Equipamento {idx}: {erro_eq}")
    else:
        resultados["erros"].append("Dados do veiculo invalidos")

    return resultados

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Handler principal da Lambda de verificacao de cadastro de motorista

    Input: event (dict) - Evento com cpf, telefone, id_motorista e modo de verificacao
    Output: (dict) - Resultado da verificacao com status e dados do motorista
    """
    logger.info("[HANDLER] Iniciando verificacao de cadastro de motorista")
    logger.info(f"[HANDLER] Event: {json.dumps(event, ensure_ascii=False)}")

    try:
        cpf = event.get('cpf')
        telefone_raw = event.get('telefone')
        id_motorista = event.get('id_motorista')
        modo = event.get('modo', 'normal')

        telefone = normalizar_telefone(telefone_raw) if telefone_raw else None
        if telefone_raw and telefone:
            logger.info(f"[VALIDACAO] Telefone normalizado: {telefone_raw} -> {telefone}")

        if modo == 'dual' or (cpf and telefone):
            logger.info("[HANDLER] Usando verificacao dual (telefone e CPF separados)")
            resultado = verificar_cadastro_dual(
                cpf=cpf,
                telefone=telefone,
                id_motorista=id_motorista
            )
        else:
            logger.info("[HANDLER] Usando verificacao normal (unica chamada)")
            resultado = verificar_cadastro_motorista(
                cpf=cpf,
                telefone=telefone,
                id_motorista=id_motorista
            )

        logger.info(f"[HANDLER] Resultado: {resultado['status'].upper()}")

        return resultado

    except Exception as e:
        logger.error(f"[ERRO] Erro critico no handler: {str(e)}", exc_info=True)
        return {
            "status": "erro",
            "tipo_erro": "handler",
            "mensagem": "Erro critico no processamento",
            "detalhes": str(e)
        }
