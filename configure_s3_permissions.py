#!/usr/bin/env python
"""
Script para configurar as permissões do bucket S3 para permitir acesso público às imagens.
Este script configura o CORS e as políticas do bucket para garantir que as imagens possam ser acessadas pela aplicação web.
"""

import os
import sys
import boto3
import django
from botocore.exceptions import ClientError
import json

# Configurar o ambiente Django
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "connect.settings")
django.setup()

from django.conf import settings

# Obter credenciais do Django settings
aws_access_key_id = getattr(settings, 'AWS_ACCESS_KEY_ID', None)
aws_secret_access_key = getattr(settings, 'AWS_SECRET_ACCESS_KEY', None)
aws_bucket_name = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', None)

if not all([aws_access_key_id, aws_secret_access_key, aws_bucket_name]):
    print("❌ Credenciais AWS não encontradas nas configurações Django.")
    sys.exit(1)

# Criar cliente S3
try:
    print(f"🔄 Conectando ao AWS S3 com credenciais:")
    print(f"  - Bucket: {aws_bucket_name}")
    
    s3 = boto3.client(
        's3',
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key
    )
    
    # Verificar política atual do bucket
    try:
        print(f"\n🔍 Verificando políticas do bucket {aws_bucket_name}...")
        
        policy = s3.get_bucket_policy(Bucket=aws_bucket_name)
        print("✅ Política atual do bucket:")
        print(policy['Policy'])
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchBucketPolicy':
            print("ℹ️ Nenhuma política encontrada para o bucket.")
        else:
            raise

    # Configurar CORS para o bucket
    print(f"\n🔄 Configurando CORS para o bucket {aws_bucket_name}...")
    cors_configuration = {
        'CORSRules': [
            {
                'AllowedHeaders': ['*'],
                'AllowedMethods': ['GET', 'HEAD'],
                'AllowedOrigins': ['*'],
                'ExposeHeaders': ['ETag', 'Content-Length'],
                'MaxAgeSeconds': 3000
            }
        ]
    }
    s3.put_bucket_cors(Bucket=aws_bucket_name, CORSConfiguration=cors_configuration)
    print("✅ CORS configurado com sucesso!")

    # Configurar acesso público para o bucket
    print(f"\n🔄 Configurando acesso público para o bucket {aws_bucket_name}...")
    
    try:
        # Desbloquear acesso público ao bucket
        s3.put_public_access_block(
            Bucket=aws_bucket_name,
            PublicAccessBlockConfiguration={
                'BlockPublicAcls': False,
                'IgnorePublicAcls': False,
                'BlockPublicPolicy': False,
                'RestrictPublicBuckets': False
            }
        )
        print("✅ Configurações de acesso público atualizadas.")
    except Exception as e:
        print(f"⚠️ Não foi possível atualizar as configurações de acesso público: {str(e)}")
        print("   Continuando com a configuração da política...")
    
    # Adicionar política de leitura pública
    bucket_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "PublicReadGetObject",
                "Effect": "Allow",
                "Principal": "*",
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{aws_bucket_name}/*"]
            }
        ]
    }
    
    s3.put_bucket_policy(Bucket=aws_bucket_name, Policy=json.dumps(bucket_policy))
    print("✅ Política de acesso público configurada com sucesso!")
    
    # Testar acesso a um objeto
    print("\n🔄 Testando acesso a um objeto...")
    
    # Listar objetos para obter um para teste
    objects = s3.list_objects_v2(Bucket=aws_bucket_name, MaxKeys=1)
    
    if 'Contents' in objects and objects['Contents']:
        test_object = objects['Contents'][0]['Key']
        url = f"https://{aws_bucket_name}.s3.amazonaws.com/{test_object}"
        print(f"✅ URL de teste para um objeto: {url}")
        print(f"   Tente acessar essa URL em seu navegador para verificar se está acessível.")
    else:
        print("ℹ️ Nenhum objeto encontrado no bucket para testar.")
    
    print("\n✅ Bucket configurado corretamente para servir arquivos de mídia!")
    print("🌐 Os arquivos devem estar acessíveis através da URL:")
    print(f"   https://{aws_bucket_name}.s3.amazonaws.com/media/nome_do_arquivo")
    
except Exception as e:
    print(f"\n❌ Erro ao configurar bucket: {str(e)}")
    sys.exit(1)
