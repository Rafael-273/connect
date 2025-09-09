#!/bin/bash

# Script para fazer backup automatizado dos arquivos de mídia
# Recomendado para uso com cron

# Configuração
BACKUP_DIR="/Users/everinnovation/Documents/CODE/FILADELFIA/connect/backups"
DAYS_TO_KEEP=30  # Quantos dias de backups manter

# Criar diretório de backup se não existir
mkdir -p $BACKUP_DIR

# Nome do arquivo de backup com timestamp
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/media_backup_$TIMESTAMP.tar.gz"

# Verificar se o volume existe
if ! docker volume ls | grep -q "media_volume"; then
  echo "❌ Volume media_volume não encontrado!"
  exit 1
fi

echo "📦 Iniciando backup dos arquivos de mídia..."

# Criar backup usando um container temporário
docker run --rm -v media_volume:/source -v $BACKUP_DIR:/backup ubuntu tar -czvf /backup/media_backup_$TIMESTAMP.tar.gz -C /source .

# Verificar se o backup foi criado com sucesso
if [ -f "$BACKUP_FILE" ]; then
  echo "✅ Backup criado com sucesso em: $BACKUP_FILE"
  
  # Limpar backups antigos
  echo "🧹 Removendo backups com mais de $DAYS_TO_KEEP dias..."
  find $BACKUP_DIR -name "media_backup_*.tar.gz" -mtime +$DAYS_TO_KEEP -delete
else
  echo "❌ Falha ao criar backup!"
fi

echo "🏁 Processo de backup concluído!"
