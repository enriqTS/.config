"""
Action group para verificacao de cadastro de motorista na API Rodosafra
Usado durante a conversa para buscar informacoes assim que o motorista fornece CPF

Funcionalidades:
- Verificacao de cadastro por CPF
- Deteccao de fraude: compara telefone da sessao com telefone do cadastro
- Se CPF existe mas telefone e diferente -> retorna status de divergencia para transbordo
- Retorna dados do motorista e veiculos cadastrados (sem expor CPF completo)
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


def _limpar_cpf(cpf: str) -> str:
    """
    Remove todos os caracteres nao numericos do CPF

    Input: cpf (str) - CPF com ou sem formatacao
    Output: (str) - CPF apenas com digitos
    """
    return re.sub(r'\D', '', cpf)


def _normalizar_telefone(telefone: str) -> str:
    """
    Normaliza telefone para formato com prefixo 55 (13 digitos)

    Input: telefone (str) - Numero em qualquer formato
    Output: (str) - Telefone normalizado com 13 digitos (55 + DDD + numero)
    """
    if not telefone:
        return ""

    telefone_limpo = re.sub(r'\D', '', str(telefone))

    if telefone_limpo.startswith('55') and len(telefone_limpo) == 13:
        return telefone_limpo
    elif len(telefone_limpo) == 11:
        return '55' + telefone_limpo
    else:
        return telefone_limpo


def _mascarar_cpf(cpf: str) -> str:
    """
    Mascara o CPF mostrando apenas os ultimos 3 digitos para privacidade

    Input: cpf (str) - CPF com 11 digitos
    Output: (str) - CPF mascarado (exemplo: ***.***.*01)
    """
    if not cpf or len(cpf) != 11:
        return "***.***.***-**"

    return f"***.***.*{cpf[-3:-2]}{cpf[-2:]}"


def _mascarar_cpf_cnpj(documento: str) -> str:
    """
    Mascara CPF ou CNPJ do proprietario do veiculo para privacidade

    Input: documento (str) - CPF (11 digitos) ou CNPJ (14 digitos)
    Output: (str) - Documento mascarado
    """
    if not documento:
        return "***"

    doc_limpo = re.sub(r'\D', '', documento)

    if len(doc_limpo) == 11:
        return f"***.***.*{doc_limpo[-3:-2]}{doc_limpo[-2:]}"
    elif len(doc_limpo) == 14:
        return f"**.***.***/****-{doc_limpo[-2:]}"
    else:
        return "***"


def verificar_motorista(params: Dict, session: Dict) -> Dict[str, Any]:
    """
    Verifica se motorista possui cadastro na API Rodosafra via CPF
    Inclui verificacao de fraude comparando telefone da sessao com telefone do cadastro

    Input: params (dict) - Parametros da funcao com cpf
           session (dict) - Atributos da sessao com dados do motorista (telefone)
    Output: (dict) - Status da verificacao e dados do motorista (sem CPF completo)

    Status possiveis:
    - "encontrado": Motorista cadastrado e telefone confere
    - "divergencia_telefone": CPF encontrado mas telefone difere (possivel fraude)
    - "nao_encontrado": CPF nao cadastrado
    - "erro": Erro na verificacao
    """
    logger.info("[VERIFICACAO] Iniciando verificacao de cadastro de motorista")

    cpf_raw = params.get('cpf') or session.get('motorista_cpf') or session.get('cpf')

    if not cpf_raw:
        logger.warning("[VALIDACAO] CPF nao fornecido")
        return {
            "status": "erro",
            "mensagem": "CPF nao fornecido"
        }

    cpf = _limpar_cpf(cpf_raw)

    if len(cpf) != 11:
        logger.warning(f"[VALIDACAO] CPF invalido - {len(cpf)} digitos")
        return {
            "status": "erro",
            "mensagem": f"CPF deve ter 11 digitos (recebido: {len(cpf)})"
        }

    logger.info(f"[VALIDACAO] CPF limpo com {len(cpf)} digitos")

    telefone = session.get('telefone') or session.get('conversa_id')

    autenticado, auth_ou_erro = autenticar_api()
    if not autenticado:
        logger.error(f"[AUTH] Falha na autenticacao: {auth_ou_erro}")
        return {
            "status": "erro",
            "mensagem": f"Erro de autenticacao: {auth_ou_erro}"
        }

    try:
        url = f"{API_BASE_URL}/publico/motorista/v1/verificar-cadastro"

        params_api = {'cpf': cpf}
        headers = {'Cookie': auth_cookie}

        logger.info(f"[API] Chamando {url}")
        logger.info(f"[API] Requisição GET para {url} com params: {json.dumps(params_api, ensure_ascii=False)}")

        response = retry_on_timeout(
            lambda: requests.get(
                url,
                params=params_api,
                headers=headers,
                timeout=15
            ),
            max_retries=3,
            operation_name="Verificar motorista",
            telefone=telefone
        )

        logger.info(f"[API] Resposta recebida - Status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            motorista = data.get('motorista', {})
            veiculo_cavalo = data.get('veiculoCavaloOuCaminhao')
            veiculo_equip1 = data.get('veiculoEquipamento1')
            veiculo_equip2 = data.get('veiculoEquipamento2')
            veiculo_equip3 = data.get('veiculoEquipamento3')
            embarque = data.get('embarque')

            logger.info(f"[VERIFICACAO] Motorista encontrado: {motorista.get('nomeCompleto')}")

            # VERIFICACAO DE FRAUDE: Compara telefone da sessao com telefone do cadastro
            telefone_sessao_normalizado = _normalizar_telefone(telefone) if telefone else ""
            telefone_cadastro = motorista.get('telefone', '')
            telefone_cadastro_normalizado = _normalizar_telefone(telefone_cadastro)

            logger.info(f"[FRAUD-CHECK] Telefone sessao: {telefone_sessao_normalizado}")
            logger.info(f"[FRAUD-CHECK] Telefone cadastro: {telefone_cadastro_normalizado}")

            if telefone_sessao_normalizado and telefone_cadastro_normalizado:
                if telefone_sessao_normalizado != telefone_cadastro_normalizado:
                    logger.warning(f"[FRAUD-CHECK] DIVERGENCIA DETECTADA - CPF cadastrado com telefone diferente")
                    logger.warning(f"[FRAUD-CHECK] Sessao: {telefone_sessao_normalizado} vs Cadastro: {telefone_cadastro_normalizado}")

                    return {
                        "status": "divergencia_telefone",
                        "mensagem": "Só um minuto que já te retorno",
                        "instrucao_chatbot": "IMPORTANTE: Foi detectada uma divergência no cadastro. Diga EXATAMENTE: 'Só um minuto que já te retorno' e chame IMEDIATAMENTE a funcao fazer_transbordo com motivo='divergencia_cadastro_cpf'. NAO mencione o problema ao motorista.",
                        "motivo_transbordo": "divergencia_cadastro_cpf",
                        "detalhes_internos": f"CPF encontrado mas telefone cadastrado ({telefone_cadastro_normalizado}) difere do telefone atual ({telefone_sessao_normalizado})"
                    }
                else:
                    logger.info(f"[FRAUD-CHECK] Telefones conferem - Cadastro validado")

            veiculos_lista = []
            if veiculo_cavalo:
                veiculos_lista.append(veiculo_cavalo)
            if veiculo_equip1:
                veiculos_lista.append(veiculo_equip1)
            if veiculo_equip2:
                veiculos_lista.append(veiculo_equip2)
            if veiculo_equip3:
                veiculos_lista.append(veiculo_equip3)

            resumo_motorista = {
                "motorista_id": motorista.get('id'),
                "nome": motorista.get('nomeCompleto'),
                "telefone": motorista.get('telefone'),
                "cnh_categoria": motorista.get('categoriaCnh'),
                "cnh_validade": motorista.get('validadeCnh'),
                "data_nascimento": motorista.get('dataNascimento'),
                "status_cadastro": motorista.get('statusCadastro'),
                "cpf_mascarado": _mascarar_cpf(motorista.get('cpf'))
            }

            veiculos_resumo = []
            for veiculo in veiculos_lista:
                if veiculo:
                    veiculos_resumo.append({
                        "veiculo_id": veiculo.get('id'),
                        "placa": veiculo.get('placa'),
                        "tipo_veiculo": veiculo.get('tipoVeiculoNome'),
                        "tipo_equipamento": veiculo.get('tipoEquipamentoNome'),
                        "eh_cavalo": veiculo.get('cavaloOuCaminhao', False),
                        "status_cadastro": veiculo.get('statusCadastro'),
                        "validade_licenciamento": veiculo.get('dataValidadeLicenciamento')
                    })

            mensagem_confirmacao = f"Encontrei um cadastro com o nome {resumo_motorista['nome']}"

            if veiculos_resumo:
                placas = ", ".join([v['placa'] for v in veiculos_resumo])
                mensagem_confirmacao += f", com os seguintes veiculos cadastrados: {placas}"

            mensagem_confirmacao += ". Essas informacoes estao corretas?"

            logger.info(f"[VERIFICACAO] Dados processados - {len(veiculos_resumo)} veiculos encontrados")

            return {
                "status": "encontrado",
                "motorista": resumo_motorista,
                "veiculos": veiculos_resumo,
                "total_veiculos": len(veiculos_resumo),
                "tem_embarque_ativo": bool(embarque),
                "mensagem_para_chatbot": mensagem_confirmacao,
                "instrucao_chatbot": "Confirme com o motorista se os dados acima estao corretos. NAO mostre o CPF completo, apenas o nome e veiculos."
            }

        elif response.status_code == 404:
            logger.info("[VERIFICACAO] Motorista nao encontrado na base de dados")

            return {
                "status": "nao_encontrado",
                "mensagem": "Nao encontrei cadastro com esse CPF",
                "instrucao_chatbot": "O motorista nao esta cadastrado. Prossiga com o cadastro normal perguntando as informacoes necessarias (nome, telefone)."
            }

        elif response.status_code == 401:
            logger.error("[API] Token invalido ou expirado")

            return {
                "status": "erro",
                "mensagem": "Token de autenticacao invalido. Tente novamente."
            }

        elif response.status_code == 500:
            logger.error("[API] Erro interno no servidor")

            log_api_error(
                api_route="/publico/motorista/v1/cadastro",
                error_code=500,
                error_message="Erro interno no servidor ao verificar motorista",
                payload={"cpf": "***"},
                response_body=response.text
            )

            return {
                "status": "erro",
                "mensagem": "Erro no servidor. Tente novamente em alguns instantes."
            }

        else:
            logger.error(f"[API] Erro HTTP inesperado: {response.status_code}")

            if response.status_code >= 500:
                log_api_error(
                    api_route="/publico/motorista/v1/cadastro",
                    error_code=response.status_code,
                    error_message=f"Erro HTTP inesperado ao verificar motorista ({response.status_code})",
                    payload={"cpf": "***"},
                    response_body=response.text
                )

            return {
                "status": "erro",
                "mensagem": f"Erro ao verificar cadastro: HTTP {response.status_code}"
            }

    except requests.exceptions.Timeout:
        logger.error("[API] Timeout na requisicao")
        return {
            "status": "erro",
            "mensagem": "Timeout ao verificar cadastro. Tente novamente."
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
    Handler principal do Lambda para action group de verificacao de motorista

    Input: event (dict) - Evento do Bedrock Agent com parametros e sessao
           context (Any) - Contexto do Lambda
    Output: (dict) - Resposta formatada para Bedrock Agent
    """
    logger.info(f"[HANDLER] Event: {json.dumps(event, ensure_ascii=False)}")
    logger.info("[HANDLER] Iniciando action group - Verificar Motorista")

    action_group = event.get('actionGroup', 'VerificarMotorista')
    function_name = event.get('function', 'verificar_motorista')

    try:
        parameters = {p.get('name'): p.get('value') for p in event.get('parameters', [])}
        session_attributes = event.get('sessionAttributes', {})

        logger.info(f"[HANDLER] Funcao: {function_name}")
        logger.info(f"[HANDLER] Atributos de sessao disponiveis: {list(session_attributes.keys())}")

        if function_name == 'verificar_motorista':
            resultado = verificar_motorista(parameters, session_attributes)
        else:
            logger.warning(f"[HANDLER] Funcao desconhecida: {function_name}")
            resultado = {
                "status": "erro",
                "mensagem": f"Funcao desconhecida: {function_name}. Use verificar_motorista"
            }

        logger.info(f"[HANDLER] Processamento concluido - Status: {resultado.get('status')}")

    except Exception as e:
        logger.error(f"[ERRO] Excecao critica no handler: {str(e)}", exc_info=True)

        resultado = {
            "status": "erro",
            "mensagem": "Ocorreu um erro ao verificar o cadastro. Por favor, tente novamente.",
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
