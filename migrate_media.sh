#!/bin/bash

# Este script deve ser executado após o primeiro deploy com os novos volumes
# Ele garante que os arquivos de mídia existentes sejam copiados para o volume persistente

# Verifica se o container web está rodando
if ! docker ps | grep -q "web-project"; then
  echo "❌ Container web-project não está rodando. Inicie-o primeiro com docker-compose up -d"
  exit 1
fi

echo "📂 Copiando arquivos de mídia existentes para o volume persistente..."

# Copia os arquivos de mídia para o volume
docker cp ./media/. $(docker-compose ps -q web-project):/usr/src/platform/media/

echo "✅ Arquivos de mídia copiados com sucesso!"
echo "🔒 Seus arquivos de mídia agora estão armazenados em um volume persistente e não serão perdidos nos deploys."
