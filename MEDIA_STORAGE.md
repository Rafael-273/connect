# Armazenamento de Mídia na Plataforma Connect

Este documento explica como as imagens e outros arquivos de mídia são armazenados e gerenciados na plataforma Connect.

## Soluções de Armazenamento

A plataforma Connect suporta duas soluções para armazenamento de mídia:

1. **Volumes Docker Persistentes** (desenvolvimento local)
2. **Amazon S3** (produção recomendada)

Você pode escolher qual solução utilizar através das configurações no arquivo `.env`.

## Armazenamento com Volumes Docker (Local)

### Como Funciona

1. Os arquivos de mídia são armazenados em um volume Docker dedicado chamado `media_volume`
2. Este volume é montado no diretório `/usr/src/platform/media` dentro do container
3. Os arquivos neste volume persistem mesmo quando os containers são recriados ou o serviço é atualizado

### Benefícios

- **Persistência**: Os arquivos não são perdidos entre deploys
- **Simplicidade**: Não é necessário configurar serviços externos como AWS S3
- **Portabilidade**: Toda a solução funciona no seu próprio servidor

### Como Configurar

Para usar o armazenamento local (padrão), certifique-se de que a seguinte configuração esteja no arquivo `.env`:

```
USE_S3=FALSE
```

## Armazenamento com Amazon S3 (Produção)

Para ambientes de produção, recomendamos o uso do Amazon S3 para armazenamento de mídia.

### Como Funciona

1. Os arquivos de mídia são enviados diretamente para um bucket S3 na AWS
2. Django usa o `django-storages` e `boto3` para gerenciar o armazenamento
3. Os arquivos são servidos diretamente do S3, reduzindo a carga no servidor da aplicação

### Benefícios

- **Escalabilidade**: S3 pode lidar com qualquer volume de arquivos
- **Confiabilidade**: S3 oferece alta disponibilidade e durabilidade
- **Performance**: Entrega de conteúdo otimizada
- **Segurança**: Controle de acesso granular

### Como Configurar

1. Crie um bucket S3 na AWS
2. Crie um usuário IAM com permissões para acessar o bucket
3. Configure as seguintes variáveis no arquivo `.env`:

```
USE_S3=TRUE
AWS_ACCESS_KEY_ID=seu_access_key_id
AWS_SECRET_ACCESS_KEY=sua_secret_access_key
AWS_STORAGE_BUCKET_NAME=nome_do_seu_bucket
```

4. Reconstrua e reinicie os containers:

```bash
docker-compose down && docker-compose up -d
```

### Migração de Mídia Local para S3

Se você já possui arquivos de mídia armazenados localmente e deseja migrar para o S3, execute o script de migração:

```bash
python migrate_to_s3.py
```

Este script irá transferir todos os arquivos existentes no diretório `/usr/src/platform/media` para o bucket S3 configurado.

## Estrutura de Diretórios

Os arquivos de mídia são organizados nos seguintes subdiretórios, independentemente do método de armazenamento escolhido:

- `/media/avatars/` - Para avatares de usuários
- `/media/event_banners/` - Para banners de eventos
- `/media/profile_pictures/` - Para fotos de perfil de membros

## Backup

### Backup de Volumes Docker

Para fazer backup dos arquivos de mídia armazenados em volumes Docker:

```bash
./backup_media.sh
```

Ou manualmente:

```bash
docker run --rm -v media_volume:/source -v $(pwd)/backups:/backup ubuntu tar -czvf /backup/media_backup_$(date +%Y%m%d).tar.gz -C /source .
```

### Backup de Arquivos no S3

Para fazer backup dos arquivos armazenados no S3, você pode usar o AWS CLI:

```bash
aws s3 sync s3://nome_do_seu_bucket/media ./backups/s3_backup_$(date +%Y%m%d)
```

## Restauração

### Restauração de Volumes Docker

Para restaurar um backup para um volume Docker:

```bash
./restore_media.sh backups/media_backup_XXXXXXXX.tar.gz
```

Ou manualmente:

```bash
docker run --rm -v media_volume:/destination -v $(pwd)/backups:/backup ubuntu bash -c "mkdir -p /destination && tar -xzvf /backup/[NOME_DO_ARQUIVO_DE_BACKUP].tar.gz -C /destination"
```

### Restauração de Arquivos para S3

Para restaurar um backup para o S3:

```bash
aws s3 sync ./backups/s3_backup_XXXXXXXX s3://nome_do_seu_bucket/media
```

## Solução de Problemas

### Problemas com Volume Docker

Se encontrar problemas com os arquivos de mídia no volume Docker:

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

### Problemas com S3

Se encontrar problemas com o armazenamento no S3:

1. Verifique se as credenciais AWS estão corretas no arquivo `.env`
2. Verifique se o bucket S3 existe e está acessível
3. Verifique as permissões do bucket (deve permitir leitura pública para objetos)
4. Verifique os logs da aplicação para erros relacionados ao S3:
   ```bash
   docker-compose logs web-project | grep -i s3
   ```
5. Teste a conexão AWS manualmente:
   ```bash
   docker-compose exec web-project python -c "import boto3; print(boto3.client('s3').list_buckets())"
   ```

## Mudando entre Local e S3

Para mudar entre armazenamento local e S3:

1. Atualize a configuração `USE_S3` no arquivo `.env`
2. Se estiver mudando de local para S3, execute o script de migração:
   ```bash
   python migrate_to_s3.py
   ```
3. Reinicie os containers:
   ```bash
   docker-compose down && docker-compose up -d
   ```

Nota: Mudanças no método de armazenamento não afetam os arquivos existentes. Use o script de migração para transferir arquivos entre os sistemas.
