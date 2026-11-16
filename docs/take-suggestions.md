# Sugestões de recortes na preparação

## Auditoria (16/11/2026)

- `ExternalMediaProjectUploadView` recebe uploads simples, multipart e câmeras; `ProjectBlockMedia.file` conserva o original no storage configurado (local/S3). As entradas pertencem ao projeto e a blocos de template ou personalizados.
- `create_project_preview` cria proxy e thumbnail em `JobWorkspace`. Celery usa `media_previews`, prefetch 1; Render Workflow também executa o mesmo corpo. Não existe cache de transcrição do original nesta etapa.
- `AIService.transcribe_segments` usa Whisper `whisper-1`, verbose JSON com palavras/segmentos. `AudioExtractor` divide áudio em MP3 mono 16 kHz; `TranscriptionService` converte offsets e remove duplicações entre chunks.
- `ExternalMediaPipeline.prepare_subtitle_tracks` transcreve o master já montado, aplica análise de conteúdo, voz de fundo e cortes de fala e gera legendas. Não é seguro substituir essa transcrição sem mapear cortes, ordem, intro, vídeos padrão e transformações.
- O modal de `project_detail.html` manipula vários intervalos e salva pelo `ExternalMediaProjectMediaTrimView`. `trim_ranges` usa milissegundos. `SourceManifestBuilder` expande cada intervalo em fonte sequencial; apenas câmeras principais entram no master.
- IA existente: serviço OpenAI compartilhado, Whisper para timestamps e `EXTERNAL_MEDIA_OFF_CONTEXT_MODEL` (padrão gpt-4.1-mini) para decisões editoriais. `OffContextAnalyzer` detecta falas descartáveis depois da montagem; não prepara os takes.

## Solução incremental

1. Cache de transcrição do original por SHA-256, projeto e idioma; progresso por chunk e lease no banco. Sugestões guardadas separadamente das marcações aprovadas.
2. Job opcional de preparação após o commit do upload, independente do preview, usando a infraestrutura existente, sem aguardar IA na requisição de upload. Falhas não invalidam preview nem impedem marcação manual.
3. Modelo identifica tentativas equivalentes e intervalos válidos por índices das palavras/segmentos. Backend escolhe a última tentativa declarada válida de cada grupo, valida ordem, limites e margens. Composições parciais exigem revisão; reordenação não é aplicada.
4. Modal existente apresenta intervalos e oferece carregamento explícito quando já houver trabalho manual. Resultado assíncrono não grava cortes; somente Salvar confirma.
5. Reuso posterior conservador: apenas quando todo o manifesto sequencial pode ser reconstruído com caches compatíveis, sem transformações de duração anteriores à transcrição. Caso contrário, manter transcrição do master.

A análise semântica depende da avaliação do modelo; timestamps de Whisper não garantem precisão de frame. Revisão humana é sempre necessária.

## Implementação entregue

- `SourceTakeAnalysis` associa hash SHA-256, idioma (incluindo detecção bilíngue), versão do algoritmo, transcrição e sugestões. O cache é **isolado por projeto**: o mesmo conteúdo em blocos diferentes reutiliza transcrição e análise; projetos diferentes têm caches independentes.
- O upload agenda análise com `transaction.on_commit`, em paralelo ao preview. Celery usa a fila `media_analysis` e o Compose inclui worker separado, concorrência 1. No Render, `process_take_analysis` usa a infraestrutura existente, mas tem orçamento próprio de timeout e duas retentativas. Não foi realizado deploy.
- O hash lê o original em blocos de 1 MB. Fontes S3 usam streaming da URL assinada existente; FFmpeg extrai áudio diretamente da fonte. Não se materializa uma segunda cópia integral do vídeo. O workspace reserva espaço proporcional ao MP3 extraído e continua protegido pelo guard de disco existente.
- A restrição única no banco e um lease com token impedem duas transcrições simultâneas do mesmo hash/idioma no projeto. Cada chunk concluído é persistido. Retentativas reaproveitam chunks e, se apenas o modelo falhar, toda a transcrição. Um processo morto depois da resposta da API e antes do checkpoint ainda pode repetir **aquele chunk**; não existe transação distribuída com Whisper.
- Whisper `whisper-1` fornece os tempos; o modelo de texto configurado em `EXTERNAL_MEDIA_OFF_CONTEXT_MODEL` (padrão `gpt-4.1-mini`) fornece grupos de tentativas e índices de palavras/segmentos, usando JSON Schema no serviço OpenAI existente.
- A escolha da última tentativa é código: para cada grupo semântico, filtra tentativas válidas e escolhe a de maior início no original, independentemente da ordem da resposta. Se não houver válida, utiliza a última composição parcial proposta pelo modelo e sinaliza revisão. A identificação de equivalência e correção continua dependendo do modelo.
- O backend limita sugestões a oito intervalos, rejeita índices/timestamps inválidos, sobreposição de fala e reordenação. Margens de 180 ms são configuráveis, limitadas pela duração e pelo trecho vizinho. Não são publicados valores de confiança.
- O modal existente recebe os intervalos, permite os ajustes usuais e só persiste pelo botão **Salvar corte**. Um estado de alteração local, a confirmação persistida (`trim_reviewed`) e uma geração por abertura do modal impedem substituição por respostas tardias. Quando já há edição, o carregamento das sugestões depende de clique explícito. Resultados da IA nunca escrevem `trim_ranges` no servidor.
- Projetos anteriores recebem análise indisponível, sem backfill pago, preservando os cortes existentes. O scheduler recupera leases expirados e uploads novos cujo worker morreu antes do hash, sem agendar projetos fora da preparação.
- O editor posterior reutiliza a transcrição quando todas as fontes habilitadas do manifesto têm cache compatível, os limites e rótulos da montagem coincidem e a duração final é compatível (tolerância máxima de 40 ms). Cada intervalo é convertido com `offset_destino + tempo_original - início_do_intervalo`, na ordem do manifesto. Cortes que atravessam palavras, fontes padrão sem cache, intros/outros, revisões existentes ou transformações incompatíveis mantêm a transcrição do master. O reuso evita também a extração de áudio quando possível.

## Arquivos da funcionalidade

| Camada | Arquivos |
|---|---|
| Cache e schema | `website/models/take_analysis.py`, `website/models/external_media.py`, `website/models/__init__.py`, migrações `0127`, `0128` e `0129` |
| Transcrição e decisões | `website/external_media/take_suggestions.py`, `website/ai/service.py`, `website/external_media/services.py` |
| Upload, consulta e aprovação | `website/views/external_media.py`, `templates/member/external_media/project_detail.html`, `static/external_media_take_suggestions.js` |
| Execução | `website/external_media/tasks.py`, `website/external_media/render_workflow.py`, `render_workflow.py`, `connect/settings.py`, `docker-compose.yml`, `.env.example` |
| Verificação | `website/test_take_suggestions.py`, `website/test_render_workflow.py`, `static/tests/external_media_take_suggestions.test.cjs` |

As alterações locais que já existiam em outros arquivos foram preservadas.

## Operação e limites

Aplicar as migrações `0127`/`0128`/`0129`, publicar os assets pelo processo normal de `collectstatic` e iniciar o worker `celery -A connect worker -Q media_analysis --concurrency=1` (ou o serviço `media-analysis-worker` do Compose). No Render, publicar também a task `process_take_analysis` no mesmo namespace da task de vídeo configurada. O Celery Beat existente executa a recuperação a cada cinco minutos. `EXTERNAL_MEDIA_TAKE_SUGGESTIONS_ENABLED=false` desativa a nova análise; `EXTERNAL_MEDIA_TAKE_PADDING_MS` controla a margem, entre 0 e 1000 ms. A chave OpenAI existente é utilizada.

A análise aceita até 10 mil palavras/segmentos e não tenta dividir arbitrariamente uma mensagem longa entre chamadas sem contexto. O trabalho tem timeout próprio (soft 6600 s/hard 6900 s no Celery; 6900 s no Render), duas retentativas e lease de duas horas. Se o áudio estiver ausente ou o serviço falhar, a marcação manual permanece disponível.

Os testes usam falas representativas com respostas semânticas controladas; não medem a qualidade real do modelo nem a precisão acústica. Ainda é necessário validar com takes reais, música, ruído e sotaques antes da ativação em produção. Não foi adicionado alinhamento forçado ou um novo classificador acústico: usam-se os timestamps disponíveis e margens conservadoras. Whisper pode alucinar fala em ruído/música, e o modelo pode avaliar tentativas incorretamente. A revisão humana continua essencial.

O ambiente não tinha PostgreSQL, Redis nem FFmpeg/FFprobe disponíveis para um ensaio completo. As migrações e os testes de integração foram exercitados em SQLite com storage local e serviços externos simulados. Não foram chamadas APIs pagas nem alterados dados de produção.

## Validações executadas

- **55 testes Python focados: passaram.** Incluem fala única, contagem técnica/intencional, silêncio nas bordas, duas/três tentativas, preferência pela última válida, tentativa posterior inválida, composição parcial de dois trechos, ausência de fala, música/ruído sem mensagem, falha de Whisper/IA, JSON/timestamps inválidos, alterações manuais durante a análise, aprovação pelo endpoint existente, cache por hash entre blocos, checkpoints de chunks, leases ativos/expirados, recuperação antes do hash, migração de marcações antigas, remapeamento e reuso pelo pipeline posterior, disparo após commit e dispatch Render.
- **4 testes Node: passaram.** Sugestões carregadas uma vez, edição manual durante análise, aprovação persistida inclusive do vídeo integral e edição depois de carregar sugestões.
- Regressão ampliada: **140 testes executados; 135 passaram, quatro falharam e um teve erro**. Os mesmos cinco problemas foram reproduzidos numa cópia do código anterior à funcionalidade, preservando as alterações locais preexistentes: duas incompatibilidades de transições de música (`from_track_id`/`from_clip_id`), textos esperados de exportação e tempo decorrido ausentes, e exposição de texto na revisão pública. Não foram alterados fluxos fora do escopo para corrigir essas falhas.
- `manage.py check`: sem problemas. `makemigrations --check --dry-run`: sem mudanças pendentes de schema. `git diff --check`: passou. `node --check` do script do modal: passou.

Comandos usados, com configuração temporária de SQLite/storage estático simples em `/tmp/filadelfia_takes_test_settings.py` (fora do repositório):

```bash
PYTHONPATH=/tmp:. .venv/bin/python manage.py test \
  website.test_take_suggestions website.test_ai_service \
  website.test_external_media.TranscriptionGroupingTests website.test_render_workflow \
  --settings=filadelfia_takes_test_settings --noinput --keepdb
node --test static/tests/external_media_take_suggestions.test.cjs
```

A regressão ampliada acrescentou `website.test_external_media.ExternalMediaProjectTests` ao comando Python. As duas últimas verificações de migração legada/recuperação anterior ao hash foram adicionadas à suíte focada após a execução ampliada e também passaram.

## Correção PostgreSQL: índices e triggers pendentes

A execução inicial da `0128` no PostgreSQL encontrou `cannot CREATE INDEX ... because it has pending trigger events`: a migração atualizava dados antes de o schema editor criar o índice diferido da nova chave estrangeira, na mesma transação. A validação inicial em SQLite não revelou esse comportamento.

A `0128` agora contém somente alterações de schema; a preservação dos dados passou para `0129_preserve_existing_take_markings`, executada depois do commit da anterior. A migração de dados trata apenas registros sem análise associada e com estado inicial `pending`, preservando análises existentes em ambientes onde a versão anterior da `0128` já havia concluído.

Validação adicional: **dois testes passaram em PostgreSQL 16 descartável**, incluindo migração de `0127` até `0129` com vídeo e cortes já cadastrados, além da preservação dos dados legados. `makemigrations --check --dry-run` também passou com PostgreSQL. O banco da aplicação não foi utilizado nesses testes.

Para retomar uma execução que falhou na `0128`, atualizar o código e executar `python manage.py migrate` novamente. A `0127` já aplicada permanece; a transação malsucedida da `0128` é revertida pelo PostgreSQL. Não é necessário marcar migração como aplicada com `--fake` nem remover colunas manualmente.

## Fila pendente sem worker de análise

Na verificação do ambiente Docker, os workers `media-worker` e `media-preview-worker` estavam ativos, mas o serviço novo `media-analysis-worker` ainda não havia sido criado. Havia um job aguardando na fila `media_analysis`; por isso o preview terminava e o modal continuava em “Analisando vídeo”.

O serviço foi iniciado e consumiu o job existente, sem reenviar o arquivo. Whisper e análise semântica concluíram com HTTP 200. A sugestão ficou disponível sem modificar os cortes aprovados.

Ao iniciar serviços individualmente, incluir também:

```bash
docker compose up -d media-analysis-worker
docker compose logs -f media-analysis-worker
```

`docker compose restart` reinicia containers existentes, mas não cria o novo serviço. O `docker compose up` completo inclui o worker definido no Compose.
