#!/usr/bin/env python3
"""
Script auxiliar para preparar o input da Lambda function
combining os arquivos equipamentos_id.json e veiculos_id.json
"""
import json
import sys
import os
from pathlib import Path

def main():
    # Caminhos dos arquivos
    base_dir = Path(__file__).parent.parent.parent
    equipamentos_file = base_dir / 'exemplos-json' / 'equipamentos_id.json'
    veiculos_file = base_dir / 'exemplos-json' / 'veiculos_id.json'
    
    # Lê os arquivos JSON
    try:
        with open(equipamentos_file, 'r', encoding='utf-8') as f:
            equipamentos = json.load(f)
        print(f"✓ Carregados {len(equipamentos)} equipamentos de {equipamentos_file}")
    except FileNotFoundError:
        print(f"⚠ Arquivo não encontrado: {equipamentos_file}")
        equipamentos = []
    except json.JSONDecodeError as e:
        print(f"✗ Erro ao ler {equipamentos_file}: {e}")
        sys.exit(1)
    
    try:
        with open(veiculos_file, 'r', encoding='utf-8') as f:
            veiculos = json.load(f)
        print(f"✓ Carregados {len(veiculos)} veículos de {veiculos_file}")
    except FileNotFoundError:
        print(f"⚠ Arquivo não encontrado: {veiculos_file}")
        veiculos = []
    except json.JSONDecodeError as e:
        print(f"✗ Erro ao ler {veiculos_file}: {e}")
        sys.exit(1)
    
    # Combina os dados
    output = {
        'equipamentos': equipamentos,
        'veiculos': veiculos
    }
    
    # Salva o arquivo de saída
    output_file = Path(__file__).parent / 'lambda-input.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n✓ Arquivo de input criado: {output_file}")
    print(f"  - Equipamentos: {len(equipamentos)}")
    print(f"  - Veículos: {len(veiculos)}")
    print(f"\nPara usar com AWS Lambda:")
    print(f"  aws lambda invoke --function-name popula-tabelas-referencia --payload file://{output_file} response.json")

if __name__ == '__main__':
    main()

