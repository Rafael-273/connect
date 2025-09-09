# Armazenamento de Mídia na Plataforma Connect

Este documento explica como as imagens e outros arquivos de mídia são armazenados e gerenciados na plataforma Connect.

## Solução Implementada

Para garantir que os arquivos de mídia (como avatares, banners de eventos e fotos de perfil) não sejam perdidos durante os deploys, implementamos uma solução usando volumes Docker persistentes.

### Como Funciona

1. Os arquivos de mídia são armazenados em um volume Docker dedicado chamado `media_volume`
2. Este volume é montado no diretório `/usr/src/platform/media` dentro do container
3. Os arquivos neste volume persistem mesmo quando os containers são recriados ou o serviço é atualizado

### Benefícios

- **Persistência**: Os arquivos não são perdidos entre deploys
- **Simplicidade**: Não é necessário configurar serviços externos como AWS S3
- **Portabilidade**: Toda a solução funciona no seu próprio servidor

## Estrutura de Diretórios

Os arquivos de mídia são organizados nos seguintes subdiretórios:

- `/media/avatars/` - Para avatares de usuários
- `/media/event_banners/` - Para banners de eventos
- `/media/profile_pictures/` - Para fotos de perfil de membros

## Migração de Arquivos Existentes

Se você já tinha arquivos de mídia antes da implementação dos volumes persistentes, execute o script de migração para transferir esses arquivos para o volume persistente:

```bash
./migrate_media.sh
```

## Backup

Recomendamos fazer backups regulares do volume Docker de mídia. Você pode usar o comando a seguir para criar um backup:

```bash
docker run --rm -v media_volume:/source -v $(pwd)/backups:/backup ubuntu tar -czvf /backup/media_backup_$(date +%Y%m%d).tar.gz -C /source .
```

Este comando criará um arquivo de backup na pasta `backups/` com a data atual.

## Restauração

Para restaurar um backup:

```bash
docker run --rm -v media_volume:/destination -v $(pwd)/backups:/backup ubuntu bash -c "mkdir -p /destination && tar -xzvf /backup/[NOME_DO_ARQUIVO_DE_BACKUP].tar.gz -C /destination"
```

## Solução de Problemas

Se encontrar problemas com os arquivos de mídia:

1. Verifique se o volume está corretamente montado:
   ```bash
   docker-compose exec web-project ls -la /usr/src/platform/media
   ```

2. Verifique as permissões dos arquivos:
   ```bash
   docker-compose exec web-project chmod -R 755 /usr/src/platform/media
   ```

3. Reinicie os containers:
   ```bash
   docker-compose down && docker-compose up -d
   ```
