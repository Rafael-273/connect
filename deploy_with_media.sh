#!/bin/bash

# Script para implantar as mudanças e garantir a persistência dos arquivos de mídia

echo "🚀 Iniciando processo de deploy com preservação de arquivos de mídia..."

# Verificar se o diretório de mídia já existe
if [ -d "./media" ]; then
  echo "📁 Diretório de mídia encontrado."
  
  # Criar pasta de backup se não existir
  mkdir -p ./backups
  
  # Fazer backup do estado atual
  TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
  echo "📦 Criando backup de segurança dos arquivos de mídia atuais..."
  tar -czvf ./backups/pre_deploy_backup_$TIMESTAMP.tar.gz -C ./media .
  echo "✅ Backup criado em ./backups/pre_deploy_backup_$TIMESTAMP.tar.gz"
else
  echo "⚠️ Diretório de mídia não encontrado. Será criado durante o processo."
fi

# Parar os containers
echo "🛑 Parando containers atuais..."
docker-compose down

# Reconstruir os containers
echo "🔨 Reconstruindo os containers para aplicar novas configurações..."
docker-compose build

# Iniciar com as novas configurações
echo "🚀 Iniciando containers com as novas configurações..."
docker-compose up -d

# Aguardar os containers estarem prontos
echo "⏳ Aguardando containers estarem prontos..."
sleep 10

# Migrar arquivos existentes para o volume
if [ -d "./media" ]; then
  echo "📤 Migrando arquivos existentes para o volume persistente..."
  ./migrate_media.sh
fi

# Verificar a configuração
echo "🔍 Verificando configuração do volume de mídia..."
./check_media_volume.sh

echo "✅ Deploy concluído com sucesso!"
echo "📝 Você pode verificar mais informações sobre o armazenamento de mídia no arquivo MEDIA_STORAGE.md"
