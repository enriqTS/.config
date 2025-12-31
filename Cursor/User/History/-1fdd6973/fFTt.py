"""
Action group para verificacao de cadastro de veiculo na API Rodosafra
Usado durante a conversa para buscar informacoes assim que o motorista fornece placa
"""
import json
import os
import re
import logging
import requests
import boto3
import time
from typing import Dict, Any, Tuple, Optional
from botocore.exceptions import ClientError
from decimal import Decimal
from datetime import datetime, timezone
from boto3.dynamodb.conditions import Key
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

dynamodb = boto3.resource('dynamodb')
NEGOCIACAO_TABLE = os.environ.get('NEGOCIACAO_TABLE', 'negociacao')
EQUIPAMENTOS_TABLE = os.environ.get('EQUIPAMENTOS_TABLE', 'equipamentos')
VEICULOS_TABLE = os.environ.get('VEICULOS_TABLE', 'veiculos')
OFERTAS_TABLE = os.environ.get('OFERTAS_TABLE', 'ofertas')


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


def _salvar_veiculos_motorista(telefone: str, veiculo_principal: Dict[str, Any],
                                equipamentos: list, session: Dict[str, Any]) -> bool:
    """
    Salva informacoes de veiculos em DynamoDB para uso posterior no embarque

    Input: telefone (str) - Telefone do motorista
           veiculo_principal (dict) - Dados do veiculo principal
           equipamentos (list) - Lista de equipamentos
           session (dict) - Atributos da sessao
    Output: (bool) - True se salvo com sucesso, False caso contrario
    """
    if not telefone:
        logger.error("[DYNAMODB] Telefone nao fornecido para salvar veiculos")
        return False

    try:
        negociacao_table = dynamodb.Table(NEGOCIACAO_TABLE)
        equipamentos_table = dynamodb.Table(EQUIPAMENTOS_TABLE)

        veiculo_id = veiculo_principal.get('veiculo_id')
        tipo_veiculo_id = veiculo_principal.get('tipo_veiculo_id')

        veiculo_cavalo_data = {
            'veiculo_id': Decimal(str(veiculo_id)) if veiculo_id is not None else None,
            'placa': veiculo_principal.get('placa'),
            'tipo_veiculo_nome': veiculo_principal.get('tipo_veiculo_nome'),
            'tipo_veiculo_id': Decimal(str(tipo_veiculo_id)) if tipo_veiculo_id is not None else None,
            'eh_cavalo': veiculo_principal.get('eh_cavalo', False)
        }

        equipamento_ids = []

        logger.info(f"[DYNAMODB] Salvando veiculos para telefone: {telefone}")
        logger.info(f"[DYNAMODB] Cavalo ID: {veiculo_id}")
        logger.info(f"[DYNAMODB] Total equipamentos: {len(equipamentos)}")

        equipamentos_salvos = 0
        equipamentos_com_erro = 0
        timestamp = datetime.utcnow().isoformat() + 'Z'

        for equip in equipamentos:
            equipamento_id = equip.get('equipamento_id')
            if not equipamento_id:
                logger.warning(f"[DYNAMODB] Equipamento sem ID, pulando: {equip.get('placa')}")
                continue

            try:
                equipamento_ids.append(equipamento_id)

                placa_equipamento = equip.get('placa')
                logger.info(f"[DYNAMODB] Salvando equipamento {placa_equipamento} (ID: {equipamento_id})")

                item_equipamento = {
                    'id_equipamento': str(equipamento_id),
                    'id_veiculo': str(veiculo_id),
                    'placa': placa_equipamento,
                    'tipo_veiculo_nome': equip.get('tipo_veiculo_nome'),
                    'tipo_veiculo_id': equip.get('tipo_veiculo_id'),
                    'tipo_equipamento_nome': equip.get('tipo_equipamento_nome'),
                    'tipo_equipamento_id': equip.get('tipo_equipamento_id'),
                    'numero': equip.get('numero'),
                    'eh_cavalo': equip.get('eh_cavalo', False),
                    'status_cadastro': equip.get('status_cadastro'),
                    'updated_at': timestamp,
                    'source': 'verificacao'
                }

                equipamentos_table.put_item(Item=item_equipamento)
                equipamentos_salvos += 1

                logger.info(f"[DYNAMODB] Equipamento {placa_equipamento} salvo com sucesso")

            except Exception as e:
                equipamentos_com_erro += 1
                logger.error(f"[DYNAMODB] Erro ao salvar equipamento {equip.get('placa')}: {str(e)}")

        logger.info(f"[DYNAMODB] Equipamentos salvos: {equipamentos_salvos}/{len(equipamentos)}")
        if equipamentos_com_erro > 0:
            logger.warning(f"[DYNAMODB] Equipamentos com erro: {equipamentos_com_erro}")

        logger.info(f"[DYNAMODB] Salvando referencias na tabela negociacao")

        update_expression_parts = []
        expression_values = {}

        update_expression_parts.append('veiculo_cavalo = :vc')
        expression_values[':vc'] = veiculo_cavalo_data

        update_expression_parts.append('veiculo_cavalo_id = :vcid')
        expression_values[':vcid'] = Decimal(str(veiculo_id)) if veiculo_id is not None else None

        equipamento_ids_decimal = [Decimal(str(eq_id)) for eq_id in equipamento_ids]
        update_expression_parts.append('equipamento_ids = :eqids')
        expression_values[':eqids'] = equipamento_ids_decimal

        # Timestamp em formato ISO 8601
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        update_expression_parts.append('veiculos_updated_at = :ts')
        expression_values[':ts'] = now_iso

        update_expression = 'SET ' + ', '.join(update_expression_parts)

        try:
            response_query = negociacao_table.query(
                KeyConditionExpression=Key('telefone').eq(telefone),
                ScanIndexForward=False,
                Limit=1,
                ProjectionExpression='tempo_sessao'
            )

            items = response_query.get('Items', [])

            if items and 'tempo_sessao' in items[0]:
                tempo_sessao = str(items[0]['tempo_sessao'])
                logger.info(f"[DYNAMODB] tempo_sessao encontrado: {tempo_sessao}")

                negociacao_table.update_item(
                    Key={
                        'telefone': telefone,
                        'tempo_sessao': tempo_sessao
                    },
                    UpdateExpression=update_expression,
                    ExpressionAttributeValues=expression_values
                )

                logger.info(f"[DYNAMODB] Referencias salvas com sucesso na tabela negociacao")
            else:
                logger.warning(f"[DYNAMODB] Nenhum tempo_sessao encontrado, criando novo registro")
                now = datetime.now(timezone.utc)
                tempo_sessao = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

                item_data = {
                    'telefone': telefone,
                    'tempo_sessao': tempo_sessao
                }
                for key, value in expression_values.items():
                    field_name = key[1:]
                    item_data[field_name] = value

                negociacao_table.put_item(Item=item_data)
                logger.info(f"[DYNAMODB] Novo registro criado na tabela negociacao")

        except Exception as e:
            logger.error(f"[DYNAMODB] Erro ao salvar na tabela negociacao: {str(e)}")
            raise

        logger.info(f"[DYNAMODB] Salvando veiculo principal na tabela veiculos")

        try:
            veiculos_table = dynamodb.Table(VEICULOS_TABLE)

            id_motorista = session.get('id_motorista') or session.get('motorista_id')

            if not id_motorista:
                logger.warning("[DYNAMODB] id_motorista nao encontrado na session")
            else:
                item_veiculo = {
                    'id_veiculo': str(veiculo_id),
                    'id_motorista': str(id_motorista),
                    'placa': veiculo_principal.get('placa'),
                    'tipo_veiculo_id': veiculo_principal.get('tipo_veiculo_id'),
                    'tipo_veiculo_nome': veiculo_principal.get('tipo_veiculo_nome'),
                    'tipo_equipamento_id': veiculo_principal.get('tipo_equipamento_id'),
                    'tipo_equipamento_nome': veiculo_principal.get('tipo_equipamento_nome'),
                    'eh_cavalo': veiculo_principal.get('eh_cavalo', False),
                    'status_cadastro': veiculo_principal.get('status_cadastro'),
                    'equipamento_ids': equipamento_ids_decimal,
                    'total_equipamentos': len(equipamento_ids),
                    'motorista_nome': session.get('motorista_nome') or session.get('nome') or 'Motorista',
                    'motorista_telefone': telefone,
                    'updated_at': timestamp,
                    'source': 'verificacao'
                }

                item_veiculo = {k: v for k, v in item_veiculo.items() if v is not None}

                veiculos_table.put_item(Item=item_veiculo)

                logger.info(f"[DYNAMODB] Veiculo principal salvo na tabela veiculos - Placa: {item_veiculo.get('placa')}")

        except Exception as e:
            logger.error(f"[DYNAMODB] Erro ao salvar veiculo principal na tabela veiculos: {str(e)}")

        return True

    except ClientError as e:
        logger.error(f"[DYNAMODB] Erro ClientError: {e.response['Error']['Code']}")
        logger.error(f"[DYNAMODB] Mensagem: {e.response['Error']['Message']}")
        return False

    except Exception as e:
        logger.error(f"[DYNAMODB] Erro ao salvar veiculos: {str(e)}")
        import traceback
        logger.error(f"[DYNAMODB] Traceback: {traceback.format_exc()}")
        return False


def _limpar_placa(placa: str) -> str:
    """
    Remove caracteres especiais da placa, deixando apenas letras e numeros

    Input: placa (str) - Placa com ou sem formatacao
    Output: (str) - Placa limpa em maiusculas
    """
    return re.sub(r'[^A-Z0-9]', '', placa.upper())


def verificar_veiculo(params: Dict, session: Dict) -> Dict[str, Any]:
    """
    Verifica se veiculo possui cadastro na API Rodosafra via placa

    Input: params (dict) - Parametros da funcao com placa
           session (dict) - Atributos da sessao com dados do motorista
    Output: (dict) - Status da verificacao e dados do veiculo (sem CPF/CNPJ proprietario)
    """
    logger.info("[VERIFICACAO] Iniciando verificacao de cadastro de veiculo")

    placa_raw = params.get('placa') or session.get('veiculo_placa') or session.get('placa')

    if not placa_raw:
        logger.warning("[VALIDACAO] Placa nao fornecida")
        return {
            "status": "erro",
            "mensagem": "Placa nao fornecida"
        }

    placa = _limpar_placa(placa_raw)

    if len(placa) != 7:
        logger.warning(f"[VALIDACAO] Placa invalida - {len(placa)} caracteres")
        return {
            "status": "erro",
            "mensagem": f"Placa deve ter 7 caracteres (recebido: {len(placa)})"
        }

    logger.info(f"[VALIDACAO] Placa limpa: {placa}")

    telefone = session.get('telefone') or session.get('conversa_id')

    autenticado, auth_ou_erro = autenticar_api()
    if not autenticado:
        logger.error(f"[AUTH] Falha na autenticacao: {auth_ou_erro}")
        return {
            "status": "erro",
            "mensagem": f"Erro de autenticacao: {auth_ou_erro}"
        }

    try:
        url = f"{API_BASE_URL}/publico/veiculo/v1/verificar-cadastro"

        params_api = {'placa': placa}
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
            operation_name="Verificar veiculo",
            telefone=telefone
        )

        logger.info(f"[API] Resposta recebida - Status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            veiculo_principal = data.get('veiculoCavaloOuCaminhao', {})
            veiculo_equip1 = data.get('veiculoEquipamento1')
            veiculo_equip2 = data.get('veiculoEquipamento2')
            veiculo_equip3 = data.get('veiculoEquipamento3')
            motorista = data.get('motorista')
            embarque = data.get('embarque')

            logger.info(f"[VERIFICACAO] Veiculo encontrado: {veiculo_principal.get('placa')}")

            eh_cavalo = veiculo_principal.get('cavaloOuCaminhao', False)
            tipo_veiculo = "cavalo" if eh_cavalo else "equipamento"

            resumo_veiculo = {
                "veiculo_id": veiculo_principal.get('id'),
                "placa": veiculo_principal.get('placa'),
                "renavam": veiculo_principal.get('renavam'),
                "tipo_veiculo_nome": veiculo_principal.get('tipoVeiculoNome'),
                "tipo_veiculo_id": veiculo_principal.get('tipoVeiculoId'),
                "tipo_equipamento_nome": veiculo_principal.get('tipoEquipamentoNome'),
                "tipo_equipamento_id": veiculo_principal.get('tipoEquipamentoId'),
                "eh_cavalo": eh_cavalo,
                "tipo": tipo_veiculo,
                "status_cadastro": veiculo_principal.get('statusCadastro'),
                "validade_licenciamento": veiculo_principal.get('dataValidadeLicenciamento'),
                "ano_exercicio": veiculo_principal.get('anoExercicio')
            }

            equipamentos_resumo = []

            for idx, equip_data in enumerate([veiculo_equip1, veiculo_equip2, veiculo_equip3], 1):
                if equip_data:
                    equipamentos_resumo.append({
                        "numero": idx,
                        "equipamento_id": equip_data.get('id'),
                        "placa": equip_data.get('placa'),
                        "tipo_veiculo_nome": equip_data.get('tipoVeiculoNome'),
                        "tipo_veiculo_id": equip_data.get('tipoVeiculoId'),
                        "tipo_equipamento_nome": equip_data.get('tipoEquipamentoNome'),
                        "tipo_equipamento_id": equip_data.get('tipoEquipamentoId'),
                        "status_cadastro": equip_data.get('statusCadastro'),
                        "eh_cavalo": equip_data.get('cavaloOuCaminhao', False)
                    })

            motorista_resumo = None
            if motorista:
                motorista_resumo = {
                    "nome": motorista.get('nome') or motorista.get('nomeCompleto'),
                    "telefone": motorista.get('telefone')
                }

            if not eh_cavalo:
                mensagem_confirmacao = f"ATENCAO: A placa {resumo_veiculo['placa']} se refere a um EQUIPAMENTO ({resumo_veiculo['tipo_veiculo_nome']}), nao a um cavalo/caminhao."

                if equipamentos_resumo:
                    mensagem_confirmacao += " Verifique se ha informacoes sobre o cavalo associado."
                else:
                    mensagem_confirmacao += " Pergunte ao motorista qual e a placa do cavalo."

                instrucao_especial = "IMPORTANTE: O motorista forneceu placa de EQUIPAMENTO quando voce pediu placa de CAVALO. Esclareça isto educadamente: 'Encontrei aqui seu conjunto, a placa [PLACA] se refere a um equipamento, qual a placa do seu cavalo?' Nao diga que ele errou - apenas esclareça."
            else:
                mensagem_confirmacao = f"Encontrei o veiculo {resumo_veiculo['placa']} cadastrado como {resumo_veiculo['tipo_veiculo_nome']}"

                if equipamentos_resumo:
                    placas_equipamentos = [e['placa'] for e in equipamentos_resumo]
                    mensagem_confirmacao += f", com os seguintes equipamentos: {', '.join(placas_equipamentos)}"

                mensagem_confirmacao += ". Esse e o conjunto completo que voce usa?"
                instrucao_especial = "Confirme com o motorista se o conjunto de veiculos acima esta correto. Pergunte se todos os equipamentos sao dele ou se falta/sobra algum. NUNCA mostre CPF/CNPJ do proprietario."

            telefone_motorista = session.get('telefone')

            if telefone_motorista:
                logger.info(f"[DYNAMODB] Salvando veiculos para motorista: {telefone_motorista}")
                salvo = _salvar_veiculos_motorista(
                    telefone=telefone_motorista,
                    veiculo_principal=resumo_veiculo,
                    equipamentos=equipamentos_resumo,
                    session=session
                )

                if salvo:
                    logger.info("[DYNAMODB] Veiculos salvos com sucesso")
                else:
                    logger.warning("[DYNAMODB] Falha ao salvar veiculos (nao critico)")
            else:
                logger.warning("[DYNAMODB] Telefone nao encontrado na session, veiculos nao salvos")

            logger.info(f"[VERIFICACAO] Dados processados - {len(equipamentos_resumo)} equipamentos encontrados")

            return {
                "status": "encontrado",
                "veiculo_principal": resumo_veiculo,
                "equipamentos": equipamentos_resumo,
                "total_equipamentos": len(equipamentos_resumo),
                "ultimo_motorista": motorista_resumo,
                "tem_embarque_ativo": bool(embarque),
                "mensagem_para_chatbot": mensagem_confirmacao,
                "instrucao_chatbot": instrucao_especial
            }

        elif response.status_code == 404:
            logger.info("[VERIFICACAO] Veiculo nao encontrado na base de dados")

            return {
                "status": "nao_encontrado",
                "placa_buscada": placa,
                "mensagem": f"Nao encontrei cadastro para a placa {placa}",
                "instrucao_chatbot": "O veiculo nao esta cadastrado. Prossiga com o cadastro normal perguntando as informacoes necessarias (tipo de veiculo, RNTRC, CPF/CNPJ do proprietario)."
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
                api_route="/publico/veiculo/v1/cadastro",
                error_code=500,
                error_message="Erro interno no servidor ao verificar veiculo",
                payload={"placa": "***"},
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
                    api_route="/publico/veiculo/v1/cadastro",
                    error_code=response.status_code,
                    error_message=f"Erro HTTP inesperado ao verificar veiculo ({response.status_code})",
                    payload={"placa": "***"},
                    response_body=response.text
                )

            return {
                "status": "erro",
                "mensagem": f"Erro ao verificar veiculo: HTTP {response.status_code}"
            }

    except requests.exceptions.Timeout:
        logger.error("[API] Timeout na requisicao")
        return {
            "status": "erro",
            "mensagem": "Timeout ao verificar veiculo. Tente novamente."
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


def verificar_compatibilidade_veiculo_carga(params: Dict, session: Dict) -> Dict[str, Any]:
    """
    Verifica se o veiculo do motorista e compativel com os requisitos da carga

    Input: params (dict) - Parametros com carga_id
           session (dict) - Atributos da sessao com telefone, veiculo_cavalo_id
    Output: (dict) - Status de compatibilidade e detalhes
    """
    logger.info("[COMPATIBILIDADE] Iniciando verificacao de compatibilidade veiculo-carga")

    # Obter carga_id
    carga_id_str = params.get('carga_id') or session.get('carga_id')

    if not carga_id_str:
        logger.warning("[COMPATIBILIDADE] carga_id nao fornecido")
        return {
            "status": "erro",
            "mensagem": "ID da carga nao fornecido"
        }

    try:
        carga_id = int(carga_id_str)
    except (ValueError, TypeError):
        logger.error(f"[COMPATIBILIDADE] carga_id invalido: {carga_id_str}")
        return {
            "status": "erro",
            "mensagem": f"ID da carga invalido: {carga_id_str}"
        }

    # Obter dados do veiculo da negociacao
    telefone = session.get('telefone') or session.get('conversa_id')

    if not telefone:
        logger.error("[COMPATIBILIDADE] Telefone nao disponivel na sessao")
        return {
            "status": "erro",
            "mensagem": "Telefone do motorista nao disponivel para buscar veiculo"
        }

    try:
        negociacao_table = dynamodb.Table(NEGOCIACAO_TABLE)

        # Buscar dados do veiculo da tabela negociacao
        response = negociacao_table.query(
            KeyConditionExpression=Key('telefone').eq(telefone),
            ScanIndexForward=False,
            Limit=1,
            ProjectionExpression='veiculo_cavalo, veiculo_cavalo_id, equipamento_ids'
        )

        items = response.get('Items', [])

        if not items:
            logger.warning(f"[COMPATIBILIDADE] Nenhum veiculo encontrado para telefone: {telefone}")
            return {
                "status": "erro",
                "mensagem": "Nenhum veiculo cadastrado encontrado. Por favor, verifique o cadastro do veiculo primeiro."
            }

        item = items[0]
        veiculo_cavalo = item.get('veiculo_cavalo', {})
        equipamento_ids = item.get('equipamento_ids', [])

        tipo_veiculo_principal = veiculo_cavalo.get('tipo_veiculo_nome')
        tipo_veiculo_id_principal = veiculo_cavalo.get('tipo_veiculo_id')
        eh_cavalo = veiculo_cavalo.get('eh_cavalo', False)

        if not tipo_veiculo_principal:
            logger.error("[COMPATIBILIDADE] Tipo de veiculo nao encontrado nos dados cadastrados")
            return {
                "status": "erro",
                "mensagem": "Tipo de veiculo nao encontrado. Verifique o cadastro do veiculo."
            }

        logger.info(f"[COMPATIBILIDADE] Veiculo principal: {tipo_veiculo_principal} (eh_cavalo={eh_cavalo})")
        logger.info(f"[COMPATIBILIDADE] Total equipamentos: {len(equipamento_ids)}")

    except Exception as e:
        logger.error(f"[COMPATIBILIDADE] Erro ao buscar veiculo: {str(e)}", exc_info=True)
        return {
            "status": "erro",
            "mensagem": f"Erro ao buscar dados do veiculo: {str(e)}"
        }

    # Buscar requisitos da carga
    try:
        ofertas_table = dynamodb.Table(OFERTAS_TABLE)

        response = ofertas_table.get_item(
            Key={'id_oferta': str(carga_id)},
            ProjectionExpression='veiculo, origem, destino, material'
        )

        oferta_item = response.get('Item')

        if not oferta_item:
            logger.warning(f"[COMPATIBILIDADE] Oferta {carga_id} nao encontrada")
            return {
                "status": "erro",
                "mensagem": f"Oferta {carga_id} nao encontrada no sistema"
            }

        veiculo_oferta = oferta_item.get('veiculo', {})
        tipos_permitidos = veiculo_oferta.get('tipos', [])
        equipamentos_requeridos = veiculo_oferta.get('equipamentos', [])

        # Informações da oferta para contexto
        origem = oferta_item.get('origem', {})
        destino = oferta_item.get('destino', {})
        material = oferta_item.get('material', 'Carga')

        origem_cidade = origem.get('endereco', {}).get('cidade', 'N/A') if isinstance(origem, dict) else 'N/A'
        destino_cidade = destino.get('endereco', {}).get('cidade', 'N/A') if isinstance(destino, dict) else 'N/A'

        logger.info(f"[COMPATIBILIDADE] Oferta: {origem_cidade} -> {destino_cidade}, Material: {material}")
        logger.info(f"[COMPATIBILIDADE] Tipos permitidos: {tipos_permitidos}")
        logger.info(f"[COMPATIBILIDADE] Equipamentos requeridos: {equipamentos_requeridos}")

    except ClientError as e:
        error_code = e.response['Error']['Code']
        logger.error(f"[COMPATIBILIDADE] Erro DynamoDB ao buscar oferta: {error_code}")
        return {
            "status": "erro",
            "mensagem": f"Erro ao buscar dados da oferta: {error_code}"
        }

    except Exception as e:
        logger.error(f"[COMPATIBILIDADE] Erro ao buscar oferta: {str(e)}", exc_info=True)
        return {
            "status": "erro",
            "mensagem": f"Erro ao buscar oferta: {str(e)}"
        }

    # Validar compatibilidade
    if not tipos_permitidos:
        logger.info("[COMPATIBILIDADE] Lista de tipos permitidos vazia - permitindo qualquer veiculo")
        return {
            "status": "compativel",
            "mensagem": "Veiculo compativel com a carga (sem restricoes de tipo)",
            "veiculo_motorista": tipo_veiculo_principal,
            "tipos_permitidos": [],
            "equipamentos_requeridos": equipamentos_requeridos,
            "oferta_detalhes": {
                "origem": origem_cidade,
                "destino": destino_cidade,
                "material": material
            }
        }

    # Caso 1: Carga requer equipamento
    if equipamentos_requeridos and len(equipamentos_requeridos) > 0:
        logger.info("[COMPATIBILIDADE] Carga requer equipamento")

        # Motorista precisa ter pelo menos um equipamento
        if not equipamento_ids or len(equipamento_ids) == 0:
            mensagem_erro = f"Seu veiculo ({tipo_veiculo_principal}) nao e compativel com esta carga. Esta carga requer equipamento ({', '.join(equipamentos_requeridos)}) e voce nao possui equipamento cadastrado."
            logger.warning(f"[COMPATIBILIDADE] Motorista nao possui equipamento: {mensagem_erro}")
            return {
                "status": "incompativel",
                "mensagem": mensagem_erro,
                "veiculo_motorista": tipo_veiculo_principal,
                "tem_equipamento": False,
                "tipos_permitidos": tipos_permitidos,
                "equipamentos_requeridos": equipamentos_requeridos,
                "motivo": "equipamento_ausente",
                "oferta_detalhes": {
                    "origem": origem_cidade,
                    "destino": destino_cidade,
                    "material": material
                }
            }

        # Buscar tipo do primeiro equipamento
        try:
            equipamentos_table = dynamodb.Table(EQUIPAMENTOS_TABLE)

            primeiro_equip_id = int(equipamento_ids[0])
            veiculo_cavalo_id = veiculo_cavalo.get('veiculo_id')

            logger.info(f"[COMPATIBILIDADE] Buscando dados do equipamento ID: {primeiro_equip_id}")

            response = equipamentos_table.get_item(
                Key={
                    'id_equipamento': str(primeiro_equip_id),
                    'id_veiculo': str(veiculo_cavalo_id)
                },
                ProjectionExpression='tipo_veiculo_nome, tipo_equipamento_nome'
            )

            equip_item = response.get('Item')

            if not equip_item:
                logger.warning(f"[COMPATIBILIDADE] Equipamento {primeiro_equip_id} nao encontrado")
                return {
                    "status": "erro",
                    "mensagem": "Erro ao buscar dados do equipamento cadastrado"
                }

            tipo_veiculo_equip = equip_item.get('tipo_veiculo_nome')
            tipo_equipamento = equip_item.get('tipo_equipamento_nome')

            logger.info(f"[COMPATIBILIDADE] Equipamento: Tipo veiculo={tipo_veiculo_equip}, Tipo equipamento={tipo_equipamento}")

            # Validar tipo de veiculo do equipamento
            if tipo_veiculo_equip not in tipos_permitidos:
                tipos_str = ', '.join(tipos_permitidos)
                mensagem_erro = f"Seu conjunto ({tipo_veiculo_equip}) nao e compativel com esta carga. Tipos aceitos: {tipos_str}"
                logger.warning(f"[COMPATIBILIDADE] Tipo de veiculo incompativel: {mensagem_erro}")
                return {
                    "status": "incompativel",
                    "mensagem": mensagem_erro,
                    "veiculo_motorista": tipo_veiculo_equip,
                    "equipamento_motorista": tipo_equipamento,
                    "tipos_permitidos": tipos_permitidos,
                    "equipamentos_requeridos": equipamentos_requeridos,
                    "motivo": "tipo_veiculo_incompativel",
                    "oferta_detalhes": {
                        "origem": origem_cidade,
                        "destino": destino_cidade,
                        "material": material
                    }
                }

            # Validar tipo de equipamento
            if tipo_equipamento not in equipamentos_requeridos:
                equips_str = ', '.join(equipamentos_requeridos)
                mensagem_erro = f"Seu equipamento ({tipo_equipamento}) nao e compativel com esta carga. Equipamentos aceitos: {equips_str}"
                logger.warning(f"[COMPATIBILIDADE] Tipo de equipamento incompativel: {mensagem_erro}")
                return {
                    "status": "incompativel",
                    "mensagem": mensagem_erro,
                    "veiculo_motorista": tipo_veiculo_equip,
                    "equipamento_motorista": tipo_equipamento,
                    "tipos_permitidos": tipos_permitidos,
                    "equipamentos_requeridos": equipamentos_requeridos,
                    "motivo": "tipo_equipamento_incompativel",
                    "oferta_detalhes": {
                        "origem": origem_cidade,
                        "destino": destino_cidade,
                        "material": material
                    }
                }

            # Tudo OK
            logger.info("[COMPATIBILIDADE] Veiculo e equipamento compativeis")
            return {
                "status": "compativel",
                "mensagem": f"Seu conjunto ({tipo_veiculo_equip} com {tipo_equipamento}) e compativel com esta carga!",
                "veiculo_motorista": tipo_veiculo_equip,
                "equipamento_motorista": tipo_equipamento,
                "tipos_permitidos": tipos_permitidos,
                "equipamentos_requeridos": equipamentos_requeridos,
                "oferta_detalhes": {
                    "origem": origem_cidade,
                    "destino": destino_cidade,
                    "material": material
                }
            }

        except Exception as e:
            logger.error(f"[COMPATIBILIDADE] Erro ao validar equipamento: {str(e)}", exc_info=True)
            return {
                "status": "erro",
                "mensagem": f"Erro ao validar equipamento: {str(e)}"
            }

    # Caso 2: Carga NAO requer equipamento - validar apenas veiculo principal
    else:
        logger.info("[COMPATIBILIDADE] Carga nao requer equipamento - validando veiculo principal")

        if tipo_veiculo_principal in tipos_permitidos:
            logger.info(f"[COMPATIBILIDADE] Veiculo principal compativel: {tipo_veiculo_principal}")
            return {
                "status": "compativel",
                "mensagem": f"Seu veiculo ({tipo_veiculo_principal}) e compativel com esta carga!",
                "veiculo_motorista": tipo_veiculo_principal,
                "tipos_permitidos": tipos_permitidos,
                "equipamentos_requeridos": [],
                "oferta_detalhes": {
                    "origem": origem_cidade,
                    "destino": destino_cidade,
                    "material": material
                }
            }
        else:
            tipos_str = ', '.join(tipos_permitidos)
            mensagem_erro = f"Seu veiculo ({tipo_veiculo_principal}) nao e compativel com esta carga. Tipos aceitos: {tipos_str}"
            logger.warning(f"[COMPATIBILIDADE] Veiculo incompativel: {mensagem_erro}")
            return {
                "status": "incompativel",
                "mensagem": mensagem_erro,
                "veiculo_motorista": tipo_veiculo_principal,
                "tipos_permitidos": tipos_permitidos,
                "equipamentos_requeridos": [],
                "motivo": "tipo_veiculo_incompativel",
                "oferta_detalhes": {
                    "origem": origem_cidade,
                    "destino": destino_cidade,
                    "material": material
                }
            }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Handler principal do Lambda para action group de verificacao de veiculo

    Input: event (dict) - Evento do Bedrock Agent com parametros e sessao
           context (Any) - Contexto do Lambda
    Output: (dict) - Resposta formatada para Bedrock Agent
    """
    logger.info(f"[HANDLER] Event: {json.dumps(event, ensure_ascii=False)}")
    logger.info("[HANDLER] Iniciando action group - Verificar Veiculo")

    action_group = event.get('actionGroup', 'VerificarVeiculo')
    function_name = event.get('function', 'verificar_veiculo')

    try:
        parameters = {p.get('name'): p.get('value') for p in event.get('parameters', [])}
        session_attributes = event.get('sessionAttributes', {})

        logger.info(f"[HANDLER] Funcao: {function_name}")
        logger.info(f"[HANDLER] Atributos de sessao disponiveis: {list(session_attributes.keys())}")

        if function_name == 'verificar_veiculo':
            resultado = verificar_veiculo(parameters, session_attributes)
        elif function_name == 'verificar_compatibilidade_veiculo_carga':
            resultado = verificar_compatibilidade_veiculo_carga(parameters, session_attributes)
        else:
            logger.warning(f"[HANDLER] Funcao desconhecida: {function_name}")
            resultado = {
                "status": "erro",
                "mensagem": f"Funcao desconhecida: {function_name}. Use verificar_veiculo ou verificar_compatibilidade_veiculo_carga"
            }

        logger.info(f"[HANDLER] Processamento concluido - Status: {resultado.get('status')}")

    except Exception as e:
        logger.error(f"[ERRO] Excecao critica no handler: {str(e)}", exc_info=True)

        resultado = {
            "status": "erro",
            "mensagem": "Ocorreu um erro ao verificar o veiculo. Por favor, tente novamente.",
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
