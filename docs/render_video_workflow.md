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

Para remoção de voz baseada em locutor, acrescente `HUGGINGFACE_TOKEN` e
`EXTERNAL_MEDIA_DIARIZATION_ENABLED=TRUE`. O token precisa ter permissão de
leitura e a conta do Hugging Face deve ter aceitado os termos do modelo
`pyannote/speaker-diarization-community-1`. A imagem do Workflow instala as
dependências CPU de PyTorch/Pyannote para esse recurso.

O banco deve aceitar conexões do Render. Arquivos de mídia precisam estar no
S3, pois o filesystem do Workflow é temporário e não é compartilhado com a
hospedagem principal.

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
