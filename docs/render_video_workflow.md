# Worker de vídeo sob demanda no Render

O site Django permanece na hospedagem principal. Quando
`RENDER_WORKFLOW_ENABLED=TRUE`, todos os jobs de Mídia Externa são enviados ao
Render Workflow: pipeline, renderização final, prévias, revisão de legendas,
masterização e exportação Premiere. Não é necessário iniciar um worker Celery
ou Celery Beat na hospedagem principal para esses fluxos.

## Serviço Render

Crie um **Workflow** no Dashboard, usando o repositório e a branch `production`:

- Runtime: `Docker`
- Dockerfile: padrão (`Dockerfile` na raiz do repositório)
- Start command: `python render_workflow.py`
- Plano da tarefa: definido no código como `4c-8g`
- Timeout: definido no código como 6 horas

O slug da tarefa registrada será exibido no Dashboard depois do primeiro deploy.
Use-o, normalmente no formato `connect-video/process_video_work`, como o
valor de `RENDER_WORKFLOW_TASK` na hospedagem principal.

## Variáveis no Workflow Render

Copie da hospedagem principal as variáveis de banco e mídia: `DB_NAME`,
`DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `USE_S3=TRUE`,
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`,
`AWS_S3_REGION_NAME`, `OPENAI_API_KEY` e as configurações
`EXTERNAL_MEDIA_*` usadas em produção.

Para o modo de streaming, configure também:

```text
EXTERNAL_MEDIA_WORKSPACE_MIN_FREE_GB=0.25
EXTERNAL_MEDIA_STREAMING_WORKSPACE_ESTIMATE_MB=512
EXTERNAL_MEDIA_REMOTE_MATERIALIZE_MAX_MB=256
EXTERNAL_MEDIA_S3_URL_EXPIRATION_SECONDS=86400
```

A expiração da URL deve ser maior que a duração máxima de uma execução. O valor
de 24 horas cobre o timeout atual de 6 horas sem criar uma URL permanente.

Para remoção de voz baseada em locutor, acrescente `HUGGINGFACE_TOKEN` e
`EXTERNAL_MEDIA_DIARIZATION_ENABLED=TRUE`. O token precisa ter permissão de
leitura e a conta do Hugging Face deve ter aceitado os termos do modelo
`pyannote/speaker-diarization-community-1`. A imagem do Workflow instala as
dependências CPU de PyTorch/Pyannote para esse recurso.

O banco deve aceitar conexões do Render. Arquivos de mídia precisam estar no
S3, pois o filesystem do Workflow é temporário e não é compartilhado com a
hospedagem principal.

A credencial do Workflow precisa de `s3:GetObject` e `s3:PutObject` para as
mídias privadas. Conceda também `s3:DeleteObject` para
`private_media/external_media/tmp/*`, pois proxies e segmentos transitórios são
apagados assim que deixam de ser necessários. Não torne o bucket público.

## Variáveis na hospedagem principal

Após um deploy de teste bem-sucedido no Render, defina:

```text
RENDER_WORKFLOW_ENABLED=TRUE
RENDER_API_KEY=<API key do Render com permissão para executar workflows>
RENDER_WORKFLOW_TASK=<slug mostrado pelo Render>
USE_S3=TRUE
```

Com `RENDER_WORKFLOW_ENABLED` ausente ou `FALSE`, o sistema preserva o Celery
atual. Se o Render estiver ativado, uma falha ao iniciar o job não executa
silenciosamente o processamento pesado na hospedagem principal.

O `CELERY_BEAT_SCHEDULE` continua no código apenas para instalações que optem
por manter Celery. No Render, cada tarefa usa `JobWorkspace` temporário e o
remove ao terminar; por isso não há um processo contínuo de limpeza para subir.

## Streaming e uso do disco

Com `USE_S3=TRUE`, os originais são entregues ao FFmpeg/FFprobe por URL S3
assinada. O S3 continua privado e suporta os HTTP Range Requests usados para
probe, seek e trim. O tamanho do original deixa de fazer parte da reserva do
`DiskCapacityGuard`.

Proxies e segmentos normalizados são criados um por vez, enviados imediatamente
para uma área temporária do S3 e removidos assim que a próxima etapa termina.
Renderizações intermediárias de áudio, cortes e overlays seguem a mesma regra.
O workspace local mantém somente a saída ativa e artefatos pequenos. URLs
assinadas não são persistidas e os parâmetros de assinatura são removidos de
diagnósticos do FFmpeg.

O fallback que materializa um objeto S3 é explícito e limitado por
`EXTERNAL_MEDIA_REMOTE_MATERIALIZE_MAX_MB`. Atualmente ele é usado por ativos
pequenos (LUT e música) e pelo pacote portátil do Premiere. Esse pacote inclui
as mídias originais por definição e, portanto, não é adequado para originais de
15 GB no Workflow com disco pequeno.

Há ainda um limite físico: a maior saída individual que o FFmpeg estiver
gravando precisa caber no scratch disponível. Um original de 15 GB pode ser
processado porque não é baixado, mas o job ainda falhará se o proxy ou vídeo
final gerado isoladamente ultrapassar o espaço livre do Workflow.

## Validação em produção

Antes de liberar para todos os usuários, execute no Workflow um projeto pequeno,
um médio e um longo (incluindo um original maior que 2 GB). Compare nos logs os
eventos `ffmpeg_io`, que registram `input_mode`, tamanho estimado da fonte S3,
duração da leitura remota, seeks solicitados, bytes da saída, uso observado do
workspace e duração da etapa. Confirme no Dashboard também CPU, RAM, duração e
custo de cada execução.

O aceite operacional é: todos os jobs finalizam, `input_mode=S3_STREAM`, nenhuma
URL assinada aparece nos logs e `local_temp_peak_usage` permanece bem menor que
o tamanho dos originais. O teste real depende dos objetos e credenciais de
produção e não deve ser substituído apenas pelos testes automatizados.
