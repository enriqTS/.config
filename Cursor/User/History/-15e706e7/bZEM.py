"""
Lambda function para popular tabelas DynamoDB com dados de equipamentos e veículos

Input: Evento com listas de equipamentos e veículos
Output: Resumo da operação com quantidade de itens inseridos
"""
import json
import logging
import boto3
import os
from typing import Dict, Any, List
from decimal import Decimal
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')

# Nomes das tabelas via variáveis de ambiente (com valores padrão)
EQUIPAMENTOS_TABLE = os.environ.get('EQUIPAMENTOS_TABLE', 'tipo_equipamentos')
VEICULOS_TABLE = os.environ.get('VEICULOS_TABLE', 'tipo_veiculos')


def convert_floats_to_decimal(obj):
    """
    Converte floats para Decimal (requerido pelo DynamoDB)
    
    Input: obj - Objeto a ser convertido (dict, list, ou valor primitivo)
    Output: Objeto convertido com floats substituídos por Decimal
    """
    if isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, dict):
        return {k: convert_floats_to_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_floats_to_decimal(item) for item in obj]
    return obj


def add_timestamps(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Adiciona timestamps de criação e atualização ao item
    
    Input: item - Dicionário do item
    Output: Item com timestamps adicionados
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    item['created_at'] = timestamp
    item['updated_at'] = timestamp
    return item


def populate_equipamentos(equipamentos: List[Dict[str, Any]], table_name: str) -> Dict[str, Any]:
    """
    Popula a tabela de equipamentos com os dados fornecidos
    
    Input: equipamentos - Lista de dicionários com dados de equipamentos
           table_name - Nome da tabela DynamoDB
    Output: Dicionário com resultado da operação
    """
    if not equipamentos:
        logger.warning("[EQUIPAMENTOS] Lista vazia fornecida")
        return {'success': True, 'count': 0, 'errors': []}
    
    table = dynamodb.Table(table_name)
    errors = []
    success_count = 0
    
    try:
        with table.batch_writer() as batch:
            for equipamento in equipamentos:
                try:
                    # Validação básica
                    if 'id' not in equipamento:
                        errors.append(f"Equipamento sem 'id': {equipamento}")
                        continue
                    
                    # Prepara o item
                    item = {
                        'id': str(equipamento['id']),
                        'nomeTipoEquipamento': equipamento.get('nomeTipoEquipamento', '')
                    }
                    
                    # Adiciona timestamps
                    item = add_timestamps(item)
                    
                    # Converte floats para Decimal
                    item = convert_floats_to_decimal(item)
                    
                    # Insere no DynamoDB
                    batch.put_item(Item=item)
                    success_count += 1
                    logger.debug(f"[EQUIPAMENTOS] Item inserido: id={equipamento['id']}")
                    
                except Exception as e:
                    error_msg = f"Erro ao inserir equipamento {equipamento.get('id', 'unknown')}: {str(e)}"
                    logger.error(f"[EQUIPAMENTOS] {error_msg}")
                    errors.append(error_msg)
        
        logger.info(f"[EQUIPAMENTOS] {success_count} equipamentos inseridos com sucesso")
        return {'success': True, 'count': success_count, 'errors': errors}
        
    except Exception as e:
        logger.error(f"[EQUIPAMENTOS] Erro crítico ao popular tabela: {str(e)}", exc_info=True)
        return {'success': False, 'count': success_count, 'errors': errors + [str(e)]}


def populate_veiculos(veiculos: List[Dict[str, Any]], table_name: str) -> Dict[str, Any]:
    """
    Popula a tabela de veículos com os dados fornecidos
    
    Input: veiculos - Lista de dicionários com dados de veículos
           table_name - Nome da tabela DynamoDB
    Output: Dicionário com resultado da operação
    """
    if not veiculos:
        logger.warning("[VEICULOS] Lista vazia fornecida")
        return {'success': True, 'count': 0, 'errors': []}
    
    table = dynamodb.Table(table_name)
    errors = []
    success_count = 0
    
    try:
        with table.batch_writer() as batch:
            for veiculo in veiculos:
                try:
                    # Validação básica
                    if 'id' not in veiculo:
                        errors.append(f"Veículo sem 'id': {veiculo}")
                        continue
                    
                    # Prepara o item
                    item = {
                        'id': str(veiculo['id']),
                        'nomeTipoVeiculo': veiculo.get('nomeTipoVeiculo', ''),
                        'cavaloOuCaminhao': veiculo.get('cavaloOuCaminhao', False),
                        'podePossuirEquipamento': veiculo.get('podePossuirEquipamento', False)
                    }
                    
                    # Adiciona timestamps
                    item = add_timestamps(item)
                    
                    # Converte floats para Decimal
                    item = convert_floats_to_decimal(item)
                    
                    # Insere no DynamoDB
                    batch.put_item(Item=item)
                    success_count += 1
                    logger.debug(f"[VEICULOS] Item inserido: id={veiculo['id']}")
                    
                except Exception as e:
                    error_msg = f"Erro ao inserir veículo {veiculo.get('id', 'unknown')}: {str(e)}"
                    logger.error(f"[VEICULOS] {error_msg}")
                    errors.append(error_msg)
        
        logger.info(f"[VEICULOS] {success_count} veículos inseridos com sucesso")
        return {'success': True, 'count': success_count, 'errors': errors}
        
    except Exception as e:
        logger.error(f"[VEICULOS] Erro crítico ao popular tabela: {str(e)}", exc_info=True)
        return {'success': False, 'count': success_count, 'errors': errors + [str(e)]}


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Handler principal do Lambda
    
    Input: event - Evento com estrutura:
           {
               "equipamentos": [...],  # Lista de equipamentos (opcional)
               "veiculos": [...]       # Lista de veículos (opcional)
           }
           context - Contexto do Lambda
    Output: Resposta com resumo da operação
    """
    logger.info(f"[HANDLER] Event recebido: {json.dumps(event, ensure_ascii=False, default=str)}")
    
    try:
        # Extrai as listas do evento
        equipamentos = event.get('equipamentos', [])
        veiculos = event.get('veiculos', [])
        
        results = {
            'equipamentos': {},
            'veiculos': {},
            'overall_success': True
        }
        
        # Processa equipamentos se fornecidos
        if equipamentos:
            logger.info(f"[HANDLER] Processando {len(equipamentos)} equipamentos")
            results['equipamentos'] = populate_equipamentos(equipamentos, EQUIPAMENTOS_TABLE)
            if not results['equipamentos']['success']:
                results['overall_success'] = False
        else:
            logger.info("[HANDLER] Nenhum equipamento fornecido")
            results['equipamentos'] = {'success': True, 'count': 0, 'message': 'Nenhum equipamento fornecido'}
        
        # Processa veículos se fornecidos
        if veiculos:
            logger.info(f"[HANDLER] Processando {len(veiculos)} veículos")
            results['veiculos'] = populate_veiculos(veiculos, VEICULOS_TABLE)
            if not results['veiculos']['success']:
                results['overall_success'] = False
        else:
            logger.info("[HANDLER] Nenhum veículo fornecido")
            results['veiculos'] = {'success': True, 'count': 0, 'message': 'Nenhum veículo fornecido'}
        
        # Verifica se pelo menos uma lista foi fornecida
        if not equipamentos and not veiculos:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'success': False,
                    'message': 'Nenhum dado fornecido. Forneça "equipamentos" e/ou "veiculos" no evento.',
                    'results': results
                }, ensure_ascii=False, default=str)
            }
        
        # Retorna resultado
        status_code = 200 if results['overall_success'] else 207  # 207 = Multi-Status (alguns sucessos, alguns erros)
        
        return {
            'statusCode': status_code,
            'body': json.dumps({
                'success': results['overall_success'],
                'message': 'Operação concluída',
                'results': results
            }, ensure_ascii=False, default=str)
        }
        
    except Exception as e:
        logger.error(f"[HANDLER] Erro crítico: {str(e)}", exc_info=True)
        return {
            'statusCode': 500,
            'body': json.dumps({
                'success': False,
                'message': f'Erro ao processar requisição: {str(e)}'
            }, ensure_ascii=False, default=str)
        }

