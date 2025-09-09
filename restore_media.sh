#!/bin/bash

# Script para restaurar backup dos arquivos de mídia

# Verificar se foi fornecido um arquivo de backup
if [ "$#" -ne 1 ]; then
    echo "❌ Uso: $0 <arquivo_de_backup.tar.gz>"
    echo "📂 Backups disponíveis:"
    ls -la /Users/everinnovation/Documents/CODE/FILADELFIA/connect/backups/ | grep "media_backup_"
    exit 1
fi

BACKUP_FILE="$1"

# Verificar se o arquivo existe
if [ ! -f "$BACKUP_FILE" ]; then
    echo "❌ Arquivo de backup não encontrado: $BACKUP_FILE"
    exit 1
fi

# Verificar se o volume existe
if ! docker volume ls | grep -q "media_volume"; then
  echo "❌ Volume media_volume não encontrado!"
  exit 1
fi

echo "⚠️ ATENÇÃO: Esta operação irá substituir todos os arquivos de mídia atuais pelos do backup."
echo "⚠️ Os dados atuais serão perdidos. Recomendamos fazer um backup antes de continuar."
read -p "🔄 Deseja continuar? (s/n): " CONFIRM

if [ "$CONFIRM" != "s" ]; then
    echo "🛑 Operação cancelada pelo usuário."
    exit 0
fi

echo "🔄 Iniciando restauração do backup: $BACKUP_FILE"

# Fazer um backup do estado atual antes de restaurar
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_DIR=$(dirname "$BACKUP_FILE")
PRE_RESTORE_BACKUP="$BACKUP_DIR/pre_restore_backup_$TIMESTAMP.tar.gz"

echo "📦 Criando backup do estado atual antes da restauração..."
docker run --rm -v media_volume:/source -v $BACKUP_DIR:/backup ubuntu tar -czvf /backup/pre_restore_backup_$TIMESTAMP.tar.gz -C /source .

echo "🔄 Restaurando arquivos do backup..."
docker run --rm -v media_volume:/destination -v $(dirname "$BACKUP_FILE"):/backup ubuntu bash -c "rm -rf /destination/* && tar -xzvf /backup/$(basename "$BACKUP_FILE") -C /destination"

echo "✅ Restauração concluída com sucesso!"
echo "📂 Um backup do estado anterior foi criado em: $PRE_RESTORE_BACKUP"
