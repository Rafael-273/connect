#!/bin/bash

# Script para verificar o status do volume de mídia

echo "🔍 Verificando configuração do volume de mídia..."

# Verificar se o volume existe
if ! docker volume ls | grep -q "media_volume"; then
  echo "❌ Volume media_volume não encontrado!"
  echo "⚠️ Execute 'docker-compose up -d' para criar o volume."
  exit 1
else
  echo "✅ Volume media_volume encontrado."
fi

# Verificar se o container web está rodando
if ! docker ps | grep -q "web-project"; then
  echo "❌ Container web-project não está rodando!"
  echo "⚠️ Execute 'docker-compose up -d' para iniciar o container."
  exit 1
else
  echo "✅ Container web-project está rodando."
fi

# Verificar se o volume está montado corretamente
if ! docker-compose exec web-project ls -la /usr/src/platform/media > /dev/null 2>&1; then
  echo "❌ Não foi possível acessar o diretório de mídia no container!"
  exit 1
else
  echo "✅ Volume montado corretamente no container."
fi

# Verificar a estrutura de diretórios
echo "📁 Verificando estrutura de diretórios..."
docker-compose exec web-project mkdir -p /usr/src/platform/media/avatars
docker-compose exec web-project mkdir -p /usr/src/platform/media/event_banners
docker-compose exec web-project mkdir -p /usr/src/platform/media/profile_pictures

# Verificar permissões
echo "🔒 Verificando permissões..."
docker-compose exec web-project chmod -R 755 /usr/src/platform/media

# Mostrar estatísticas
echo "📊 Estatísticas do volume de mídia:"
docker-compose exec web-project du -sh /usr/src/platform/media
docker-compose exec web-project find /usr/src/platform/media -type f | wc -l | xargs echo "Total de arquivos:"

echo "✅ Verificação concluída com sucesso! O volume de mídia está configurado corretamente."
