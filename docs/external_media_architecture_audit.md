# Auditoria Arquitetural do Motor de Edicao Audiovisual

Data da auditoria: 2026-08-31

Escopo analisado: modulo `website.external_media`, modelos, formularios, views, tasks Celery,
templates relacionados, FFmpeg, timeline interna, exportacao Premiere e testes automatizados.

Esta auditoria descreve o codigo atual. Ela nao aplica refatoracoes ao motor.

## 1. Executive Summary

O sistema atual nao e apenas um `AnnouncementEditor`. O nucleo ja possui capacidades
reutilizaveis para montagem por blocos, trim, transcricao, traducao, legendas, Auto
Reframe, LUT, edicao de fala, musica, ducking, tratamento de dialogo, masterizacao,
controle de qualidade e exportacao para Premiere. A busca por termos de dominio mostrou
pouquissimas condicionais por categoria de projeto. Esse e um bom ponto de partida para
Podcast, Reel, Testemunho e outros formatos.

O principal limite nao e o acoplamento nominal a "Anuncio Mensal", mas a forma como a
coordenacao foi crescendo:

- `ExternalMediaProjectPipeline` e `ExternalMediaPipeline`, no mesmo arquivo de 2.902
  linhas, concentram orquestracao, decisoes editoriais, persistencia e execucao;
- a sequencia e fixa e parcialmente hardcoded, embora seja habilitada por configuracao;
- o template mistura intencao, assets, configuracoes tecnicas e flags de execucao;
- decisoes de edicao ficam dispersas em `ExternalMediaProject.configuration`;
- a `InternalTimeline` e gerada depois do processamento apenas para o Premiere; ela nao
  governa o render final;
- render e exportacao recompõem a edicao por caminhos diferentes e ja divergem em casos
  reais;
- analises caras nao possuem artefatos versionados nem cache por hash de source/config;
- tasks possuem boa protecao contra concorrencia, mas nao checkpoint por capability.

Recomendacao central: manter o monolito Django e evoluir incrementalmente. O primeiro
passo nao deve ser um plugin system nem uma reescrita do pipeline. Deve ser estabilizar
uma representacao unica e testavel das decisoes editoriais, fazer render e exportacao
consumirem essa representacao e somente depois introduzir um plano de capacidades.

### Resposta curta a pergunta principal

Um Podcast simples, de uma camera, ja poderia reutilizar boa parte do motor com um novo
template e configuracoes. Um Podcast multicamera ainda exigiria capacidades novas de
sincronizacao e selecao de camera, mas nao deveria exigir outro motor. Um Reel simples
tambem reutilizaria preset, trim, reframe, legenda, musica e render; captions dinamicas e
decisoes de ritmo ainda seriam codigo novo. Testemunho ja usa capacidades existentes,
embora a remocao de entrevistador esteja modelada como flag especifica de bloco.

Hoje, portanto:

```text
Novo formato simples = principalmente configuracao + alguns ajustes
Novo formato com multicamera/B-roll = novas capacidades + mudancas no pipeline atual
```

O objetivo realista depois da migracao proposta e:

```text
Novo formato = EditPlan produzido por Template + capacidades existentes
```

## 2. Current Architecture

### 2.1 Componentes reais

```text
UI membro/admin
  -> Views Django + Forms
  -> Models: Project, TemplateVersion, Blocks, Uploads, Job, Tracks, Assets
  -> Celery task de alto nivel
  -> ExternalMediaProjectPipeline
       -> TemplateService / ProjectService
       -> VideoAssemblyService
       -> ExternalMediaPipeline (legenda/edicao de fala legado)
       -> Audio services
       -> Quality control
       -> RenderService
       -> StorageService
  -> Assets finais

Exportacao Premiere (fluxo separado)
  -> InternalTimelineBuilder
  -> PremiereExporter
  -> pacote ZIP + timeline.json + FCP 7 XML
```

### 2.2 Fluxo de projeto realmente executado

```text
Criar projeto a partir da ultima MediaTemplateVersion
  -> copiar default_settings para Project.configuration
  -> upload por bloco / bloco personalizado
  -> gerar proxy individual assincrono para cada upload
  -> validar uploads e assets do template
  -> resolver plugins efetivos
  -> inicializar ProjectPipelineStep
  -> materializar originais, defaults e assets do template em diretorio temporario
  -> se houver legenda: gerar proxies dos takes
  -> normalizar, trimar, aplicar LUT/reframe e concatenar proxies
  -> criar ExternalMediaJob derivado
  -> extrair audio em chunks
  -> transcrever com timestamps
  -> diarizar/remover voz de fundo em blocos marcados
  -> detectar silencio e vicios; produzir SpeechEditPlan
  -> remapear palavras, blocos e ranges protegidos
  -> criar SubtitleTrack/Cues e traduzir
  -> remontar o master a partir dos originais
  -> reaplicar planos de reframe do proxy no original
  -> combinar cortes de voz de fundo + fala em uma passagem
  -> tratar dialogo -> mixar musica/ducking -> masterizar
  -> validar midia e legendas
  -> gravar o master derivado em ExternalMediaJob.original_video
  -> gerar SRT/VTT e queimar legendas no video
  -> salvar MediaAssets
  -> finalizar Project e Job
```

O fluxo sem legendas existe em `ExternalMediaProjectPipeline.run`, mas hoje e, na
pratica, inalcançavel para projetos de template porque `TemplateService.enabled_plugins`
sempre inclui `subtitle_pt`.

### 2.3 Papel do Celery

Celery e executor assincrono, nao orquestrador de etapas. Uma task de alto nivel chama
servicos sincronos ate o final. Nao ha chain/chord por capability, checkpoint ou retry
individual. `autoretry_for=()` desativa retry automatico.

Existe uma protecao importante:

- advisory lock PostgreSQL serializa tasks por projeto;
- `celery_task_id` rejeita execucoes antigas;
- projeto finalizado/cancelado impede reexecucao tardia.

Isso reduz concorrencia, mas uma redelivery com o mesmo task id apos crash pode reiniciar
o fluxo inteiro porque nao ha checkpoint de capability.

### 2.4 Progresso

O progresso e persistido em dois niveis:

- `ExternalMediaProject.status/progress/current_step`;
- `ProjectPipelineStep` por codigo de etapa;
- `ExternalMediaJob` repete status/progresso do subpipeline de legendas/render.

Os percentuais sao mapeados manualmente nos services. Nao derivam do custo real nem de
um grafo de execucao. `timed_step` registra duracao no log para apenas algumas operacoes,
sem persistir metrica por capability.

## 3. Existing Capabilities

Legenda de classificacao:

- `GENERIC`: recebe midia/configuracao sem conhecer projeto/template;
- `MOSTLY_GENERIC`: algoritmo reutilizavel, mas integracao depende de modelos atuais;
- `DOMAIN_COUPLED`: conhece blocos/templates ou semantica especifica;
- `HIGHLY_COUPLED`: mistura analise, decisao, persistencia e execucao do fluxo atual.

### 3.1 ANALYSIS

| Capacidade | Implementacao | Entrada -> saida | Dependencias/chamadores | Efeitos | Classe | Problemas |
|---|---|---|---|---|---|---|
| Probe de midia | `RenderService`, `VideoAssemblyService`, `MediaQualityService`, `InternalTimelineBuilder` | arquivo -> dimensoes, streams, duracao, cor | FFprobe; chamada repetidamente por montagem, QC e exportacao | nenhuma persistencia propria | MOSTLY_GENERIC | Implementacao/probe duplicado e sem cache por arquivo |
| Extracao de audio | `AudioExtractor`, `SpeechEditService`, tasks de mastering | video -> WAV/chunks | FFmpeg | cria temporarios | GENERIC | O mesmo audio pode ser extraido mais de uma vez na mesma execucao |
| Transcricao | `TranscriptionService` | chunks -> segmentos/palavras com timestamps | `AIService`; chamado pelo pipeline de legendas | chamada externa; sem artifact persistido | MOSTLY_GENERIC | Provider nao possui contrato explicito; resultado so sobrevive como cues/plano |
| Deteccao de silencio | `SpeechEditAnalyzer` + `AudioActivity` | palavras + WAV + config -> `SpeechCut[]` | pipeline de fala | nenhuma alteracao direta | MOSTLY_GENERIC | Politica e detector ainda vivem na mesma classe |
| Deteccao de vicios | `SpeechEditAnalyzer` | palavras/WAV/termos -> `SpeechCut[]` | template filler terms/config | nenhuma alteracao direta | MOSTLY_GENERIC | Detecta e decide remover no mesmo passo; sem confidence/provenance |
| Diarizacao | `CommunitySpeakerDiarizer` | WAV -> `SpeakerTurn[]` | pyannote/Hugging Face | modelo carregado por processo | GENERIC | Forca 2 speakers; sem artifact/cache/versionamento do analyzer |
| Voz de fundo/entrevistador | `BackgroundVoiceRemovalService` | video + palavras + ranges -> plano de cortes | diarizacao e fallback por amplitude | cria WAV temporario | DOMAIN_COUPLED | Detector tambem escolhe "featured speaker" e decide remover; limitado semanticamente a testemunho |
| Analise de audio | `AudioAnalysisService` | audio/video -> LUFS, true peak, LRA | FFmpeg loudnorm | nenhuma | GENERIC | Resultados nao sao reutilizados entre QC, mix e mastering |
| Analise para Auto Reframe | `AutoReframeService` | video + target -> plano/keyframes | OpenCV/detectores | nenhuma persistencia direta | MOSTLY_GENERIC | Plano e config nao possuem artifact/version formal; versao e constante isolada |
| Controle de qualidade | `MediaQualityService` | midia/cortes/tracks -> `QualityReport` | FFprobe, FFmpeg, audio analysis | metricas gravadas pelo orquestrador | GENERIC | Bom limite; faltam validacoes de paridade render/timeline e de conteudo no upload |
| Retake detection | Nao implementado | - | - | - | AUSENTE | Necessario para Podcast/Live, deve produzir candidatos e nao cortes finais |
| Shot/person/B-roll analysis | Nao implementado como capability | - | - | - | AUSENTE | Auto Reframe detecta pessoas internamente, mas nao publica mapa reutilizavel |

### 3.2 EDITING E VISUAL

| Capacidade | Implementacao | Entrada -> saida | Dependencias/chamadores | Efeitos | Classe | Problemas |
|---|---|---|---|---|---|---|
| Trim manual | `ProjectBlockMedia.trim_*`, views/forms, assembly | source + in/out -> source range | UI e FFmpeg | banco | MOSTLY_GENERIC | Boa decisao nao destrutiva, mas nao existe entidade/source manifest canonica |
| Ordem de takes | `ProjectBlockMedia.position` | lista -> ordem | UI/montagem | banco | GENERIC | Funciona por bloco |
| Ordem de blocos | `configuration.block_order` | ids -> ordem | UI e `_materialize` | JSON de projeto | MOSTLY_GENERIC | Timeline Premiere ignora esta ordem |
| Montagem por blocos | `VideoAssemblyService` + `_materialize` | sources -> master concatenado | template/project | arquivos temporarios | MOSTLY_GENERIC | Descoberta de sources esta dentro do orquestrador; concat e decisoes misturados |
| Bloco padrao/intacto | `MediaTemplateBlock` flags | config -> ranges protegidos | assembly, fala, audio, legenda | JSON de projeto | DOMAIN_COUPLED | Flag agrega varias politicas distintas; "intacto" nao e uma capability explicita |
| Corte de fala | `SpeechEditPlan` + `SpeechEditService` | cuts -> video reeditado | FFmpeg | gera derivado e substitui arquivo do Job | MOSTLY_GENERIC | Plano e bom nucleo, mas nao agrega toda decisao numa fonte canonica |
| Remocao de entrevistador | `BackgroundVoiceRemovalService` | speaker turns -> cuts | bloco `remove_background_voice` | plano + video intermediario | DOMAIN_COUPLED | Analise, decisao e aplicacao acopladas na integracao |
| Auto Reframe/tracking | `AutoReframeService` + assembly | video -> crop keyframes -> video | FFmpeg/OpenCV | plano em JSON; encode | MOSTLY_GENERIC | Analise e execucao sao separaveis, mas integracao esta dentro da normalizacao |
| Normalizacao/crop/scale | `VideoAssemblyService._normalize` | take -> take padronizado | FFmpeg | intermediario | GENERIC | Tambem decide HDR, LUT, reframe e preservacao no mesmo metodo |
| HDR -> SDR | `_normalize` e `RenderService` | HDR -> BT.709 | FFmpeg | encode | GENERIC | Logica duplicada em montagem e render de legenda |
| LUT | `LUTService` + `_normalize` | LUT/config -> video | FFmpeg `lut3d` | intermediario | MOSTLY_GENERIC | Timeline apenas documenta asset; intensidade diverge (`1.0` no export) |
| Estilo/render de captions | `SubtitleService` + `RenderService` | cues/style -> ASS/video | FFmpeg/libass | SRT/VTT/ASS/video | MOSTLY_GENERIC | Conteudo/timing estao bem separados; classe tambem concentra muita formatacao ASS |
| Overlay/B-roll/multicam | Nao implementados no render | - | - | - | AUSENTE | Timeline reserva tracks, mas isso nao equivale a suporte funcional |

### 3.3 AUDIO

| Capacidade | Implementacao | Entrada -> saida | Dependencias/chamadores | Efeitos | Classe | Problemas |
|---|---|---|---|---|---|---|
| Tratamento de dialogo | `DialogueProcessor` | mix original + speech blocks -> audio/video tratado | FFmpeg | derivado + metricas | GENERIC | Separacao adequada; medicao por bloco tem limite de seguranca |
| Loop/crossfade de musica | `AudioMixingService` | musica + duracao -> bed continuo | FFmpeg | derivado | GENERIC | Premiere cria repeticoes sem reproduzir o mesmo acrossfade |
| Ducking | `AudioMixingService` | speech blocks + musica -> automacao/mix | cues, FFmpeg | derivado + metricas | GENERIC | Depende de cues como proxy de fala; nao de `AnalysisArtifact` |
| Ducking espectral | `AudioMixingService` | blocos + densidade -> EQ por janela | FFmpeg | derivado | GENERIC | Limitado a 40 blocos por tamanho do filtro; fallback silencioso |
| Mixagem | `AudioMixingService` | dialogo + musica -> mix | FFmpeg | derivado | GENERIC | Bom limite de responsabilidade |
| Masterizacao | `AudioMasteringService` | mix final + perfil -> master | FFmpeg | derivado + metricas | GENERIC | Ja existe tambem como feature independente, sinal positivo de reuso |
| Mux de audio | `AudioMuxingService` | video + audio -> video | FFmpeg | derivado | GENERIC | Adequadamente isolado |

### 3.4 TIMELINE, OUTPUT E INFRASTRUCTURE

| Capacidade | Implementacao | Entrada -> saida | Dependencias/chamadores | Efeitos | Classe | Problemas |
|---|---|---|---|---|---|---|
| Internal Timeline | `InternalTimelineBuilder` | Project/config/models -> JSON v1 | usada pela exportacao | copia/probe/extrai assets | MOSTLY_GENERIC | Nao e SSOT; recomputa edicao e omite decisoes atuais |
| Preview de upload | `create_project_preview` | upload -> proxy MP4 | Celery/assembly | storage + status | GENERIC | Task e resiliente a upload apagado; sem hash/reuso |
| Preview de revisao | `create_subtitle_review_preview` | job source -> proxy | Celery | storage | MOSTLY_GENERIC | Nao trata sessao removida e pode deixar arquivos substituidos |
| Render final | `RenderService` e pipelines | master + tracks -> assets | FFmpeg | storage/banco | MOSTLY_GENERIC | Renderer nao conhece categoria, o que e bom; nao recebe Timeline |
| Export de legendas | `SubtitleService` | track -> SRT/VTT/ASS | render/export | arquivos | GENERIC | Boa separacao de conteudo e estilo nos modelos |
| Premiere export | `InternalTimelineBuilder`, `PremiereExporter` | Project -> JSON/XML/ZIP | Celery | storage/banco | MOSTLY_GENERIC | Caminho editorial paralelo ao render; paridade incompleta |
| Storage | `StorageService`, FileFields | local/storage -> assets | Django storage | storage/banco | GENERIC | `replace_file_safely` e robusto; outros fluxos ainda deletam antes de salvar |
| FFmpeg adapter | `FFmpegRunner` | argv -> processo | todos os services | processo externo | GENERIC | Seguro contra shell injection; falta telemetria estruturada e classificacao de recursos |
| Orquestracao | `ExternalMediaProjectPipeline` | projeto -> resultado final | models/services | banco/storage/FFmpeg/API | HIGHLY_COUPLED | Classe conhece quase todas as fases e percentuais |

## 4. Coupling Analysis

### 4.1 Acoplamento ao dominio de anuncio

O acoplamento textual e baixo. Categoria `ANNOUNCEMENT` aparece nos choices de template e
musica, nao em condicionais do pipeline. Nao existem pipelines separados por tipo.

Os acoplamentos reais sao:

1. Modelo por blocos ordenados, que se ajusta bem a anuncio, mas nao representa tracks
   simultaneas, multicamera ou B-roll.
2. `remove_background_voice` embute uma politica de testemunho no modelo de bloco.
3. Prompt de traducao menciona sermoes e anuncios de igreja.
4. Plugins possuem um enum fechado com nove capacidades historicas.
5. Labels e default behavior pressupõem PT/EN.

### 4.2 Template e execucao

`MediaTemplateVersion` e um snapshot protegido no projeto, o que e correto. Entretanto,
ele combina quatro naturezas:

- intencao editorial: blocos, plugins, idiomas;
- assets: intro, outro, LUT, musica, videos padrao;
- configuracao tecnica: codecs/preset, audio, estilos;
- politicas: flags de fala, reframe e comportamento de bloco.

`TemplateService.enabled_plugins` nao e apenas leitor. Ele reinterpreta o template:

- ignora registros de subtitle/LUT/intro/outro/music;
- sempre adiciona `subtitle_pt`;
- infere traducao por `output_languages`;
- infere LUT e musica pela existencia de arquivo;
- adiciona audio como strings fora do enum;
- nao volta a adicionar intro/outro.

Consequencias:

- o branch sem legendas e efetivamente morto;
- intro/outro podem existir e estar habilitados, mas nao entrar no render;
- a Timeline consulta plugins diretamente e pode exportar intro/outro que o render nao usou;
- existem duas definicoes distintas de "capability habilitada".

Prioridade: HIGH. Corrigir isso exige primeiro testes de comportamento para nao alterar
acidentalmente templates existentes.

### 4.3 Dominio e infraestrutura

Aspectos positivos:

- comandos sao listas e passam por `FFmpegRunner`, sem `shell=True`;
- storage usa API Django e diretorios temporarios;
- mixagem, mastering, dialogo e mux estao em services separados;
- `PremiereExporter` recebe JSON neutro e nao consulta models.

Misturas ainda existentes:

- `VideoAssemblyService._normalize` decide trim, framing, reframe, HDR, LUT, codecs e
  executa tudo em FFmpeg;
- `ExternalMediaProjectPipeline` consulta models, decide fluxo, executa services, grava
  configuracao e calcula progresso;
- `InternalTimelineBuilder` consulta models, copia assets, faz probe, extrai WAV e gera
  uma representacao de dominio;
- `ExternalMediaPipeline` transcreve, decide cortes, altera arquivos/job, persiste planos,
  cria captions, traduz e renderiza.

## 5. Internal Timeline Assessment

### 5.1 O que ela ja representa

A schema `connect.internal_timeline.v1` possui:

- assets com caminhos portateis e metadata;
- `source_in/out` e `timeline_in/out`;
- multiplas video/audio tracks reservadas;
- clips divididos por cortes;
- transforms/keyframes de Auto Reframe;
- dialogue WAV separado;
- musica e automacao de volume;
- captions editaveis e overlay alpha de referencia;
- LUT/effects/compatibility;
- markers e uma lista inicial de decisions.

Ela e uma base valiosa e nao deve ser descartada.

### 5.2 Por que ainda nao e Single Source of Truth

- e construida somente quando o usuario pede exportacao;
- o render final nao a consome;
- ela relê models e `project.configuration` para reconstruir decisoes;
- nao e persistida por revision junto ao render;
- nao identifica qual schema/config/analyzer gerou cada decisao;
- nao possui operacoes manuais/automaticas com prioridade;
- nao modela transicoes, overlays, B-roll ou cameras de forma funcional;
- nao possui uma track graph usada pelo executor.

### 5.3 Divergencias confirmadas

1. Blocos personalizados nao aparecem em `_sources`.
2. `configuration.block_order` nao e aplicado.
3. Apenas `speech_edit_plan` e lido; `background_voice_plan` nao e combinado.
4. Plugin efetivo e lido de forma diferente do render.
5. LUT e documentado com intensidade 1.0, ainda que o template use intensidade parcial.
6. Musica no Premiere repete clips sem representar exatamente o `acrossfade` do render.
7. A montagem final pode usar planos normalizados/combinados que a Timeline recompõe de
   outra maneira.

Logo, hoje nao e seguro afirmar:

```text
Preview = Render = Premiere Export
```

## 6. Pipeline Assessment

### 6.1 Existe pipeline central?

Sim, em dois niveis:

- `ExternalMediaProjectPipeline`: projeto/template/montagem/finalizacao;
- `ExternalMediaPipeline`: engine legado de transcricao, fala, traducao e captions.

### 6.2 A sequencia e hardcoded?

Sim. Plugins habilitam partes, mas a ordem de chamada esta escrita em `run`, `render`,
`prepare_subtitle_tracks` e `_prepare_final_master`. `ProjectService.STEP_SEQUENCE`
organiza apenas a apresentacao/progresso; ele nao executa as etapas.

### 6.3 O Template controla o pipeline?

Parcialmente. Ele fornece flags/config/assets, mas `TemplateService` injeta regras
implicitas e o orquestrador decide a sequencia. Categoria/tipo nao controla o fluxo.

### 6.4 Dependencias atuais, hoje implicitas

```text
Assembly -> RenderJob
Transcription -> Speech editing
Transcription -> Subtitle grouping
Source captions -> Translation
Subtitle cues -> Dialogue leveling and ducking
Proxy reframe plan -> Final original assembly
Speech plan -> Final combined cuts
Final mix -> Mastering
Final master + tracks -> Burn-in render
Project/config/models -> Premiere timeline
```

Nao existe validacao previa de dependencias. O codigo garante a ordem por chamadas
diretas.

### 6.5 Falhas e retries

- excecoes de dominio geram status ERROR e mensagem;
- excecoes inesperadas sao logadas e convertidas em erro generico no projeto;
- tasks nao fazem autoretry;
- o usuario pode reenfileirar/reprocessar;
- advisory lock e task id evitam concorrencia e tasks antigas;
- nao ha compensacao transacional para todo o fluxo;
- temporarios sao limpos automaticamente;
- arquivos finais usam substituicao segura em alguns caminhos.

## 7. Reusability Assessment

### 7.1 Ja reutilizavel

- FFmpegRunner;
- AudioAnalysis, AudioMixing, AudioMastering, AudioMuxing;
- DialogueProcessor;
- SubtitleTrack/Cue e SubtitleService;
- TranslationService, apesar do prompt/provider atual;
- SpeechEditPlan e SpeechEditService;
- AutoReframeService;
- RenderService;
- MediaQualityService;
- PremiereExporter, desde que receba timeline correta;
- presets, estilos e perfis de mastering.

### 7.2 Reutilizavel com pequena extracao

- descoberta/materializacao de sources;
- resolucao de capabilities efetivas;
- politicas de silencio/filler;
- background voice como `SpeakerRemovalPolicy` generica;
- reframe plan como artifact;
- construcao de speech blocks;
- manifest de assets e decisoes.

### 7.3 Nao existe ainda

- retake candidates;
- shot map;
- speaker map persistido;
- face/person track persistido;
- multicam sync;
- camera selection;
- B-roll analysis/search/placement;
- overlay operations;
- derived clips/Reels;
- undo/override generico;
- analysis artifact/cache;
- executor de Timeline.

## 8. Technical Debt

| Prioridade | Problema | Impacto/risco | Esforco | Beneficio |
|---|---|---|---|---|
| CRITICAL | Render e Premiere nao consomem a mesma edicao | Export pode omitir/reordenar midia e cortes | Medio | Confiabilidade editorial e base para editor futuro |
| HIGH | Resolucao de plugins diverge e sempre habilita legenda | Fluxos/configuracoes nao refletem o template; intro/outro inconsistentes | Pequeno/medio | Templates previsiveis e branch sem legenda real |
| HIGH | Decisoes ficam em chaves soltas de `Project.configuration` | Sem schema, provenance, invalidation ou compatibilidade garantida | Medio | Testabilidade e evolucao segura |
| HIGH | Redelivery reinicia a task inteira sem checkpoint | Pode repetir analises, criar jobs/intermediarios e gastar recursos | Medio | Idempotencia e recuperacao |
| HIGH | Timeline omite custom blocks/order/background voice | Premiere diverge de projetos reais | Pequeno/medio | Paridade imediata |
| HIGH | Analises caras nao sao versionadas/cacheadas | Reprocessa transcricao, diarizacao, tracking e probe | Medio/alto | Performance e reproducibilidade |
| MEDIUM | Dois pipelines e dois estados Project/Job | Duplicacao de progresso, falha e persistencia | Alto | Menos complexidade, mas migracao arriscada |
| MEDIUM | `services.py` concentra 2.902 linhas | Descoberta, ownership e testes mais dificeis | Medio | Manutencao; nao exige mudar comportamento |
| MEDIUM | Probe/extracao de audio duplicados | CPU/I/O redundante | Pequeno/medio | Processamento menor |
| MEDIUM | Upload valida extensao/tamanho, nao conteudo | Arquivo invalido falha tarde; custo desperdicado | Pequeno | Feedback e seguranca operacional |
| MEDIUM | Progressos percentuais manuais | UX trava em etapas longas | Medio | Progresso explicavel por capability |
| MEDIUM | Provenance incompleta | Nao ha undo confiavel nem explicacao da IA | Medio | Editor e override futuro |
| LOW | Prompt de traducao conhece igreja/anuncio | Menor reuso fora do dominio atual | Pequeno | Generalidade de idioma/contexto |
| LOW | Categoria de musica inclui announcement | Apenas taxonomia | Pequeno | Sem impacto arquitetural relevante |

### 8.1 Analise, decisao, Timeline e execucao

A separacao existe em graus diferentes:

- `SpeechEditAnalyzer` produz `SpeechEditPlan` e `SpeechEditService` executa: boa base;
- `AutoReframeService` produz keyframes e assembly executa: boa base, embora integrada
  dentro da normalizacao;
- `BackgroundVoiceRemovalService.analyze` faz diarizacao, escolhe o locutor principal e
  aplica uma politica de remocao na mesma capability;
- `ExternalMediaPipeline._apply_speech_edit` analisa, decide, persiste, remapeia e, fora
  do proxy pipeline, executa;
- `InternalTimelineBuilder` traduz decisoes depois que o render ja foi planejado/executado.

O alvo deve ser:

```text
Analyzer -> facts/candidates
Policy -> decisions
EditDecisionSet -> operations normalizadas
Timeline -> source/timeline mapping
Executor -> FFmpeg
```

Nao e necessario separar isso em processos ou servicos de rede.

### 8.2 Imutabilidade dos sources

Os uploads em `ProjectBlockMedia.file` e os videos padrao do template sao preservados e
reutilizados no reprocessamento. O trim referencia timestamps, sem alterar esses arquivos.
Esse e o comportamento correto.

`ExternalMediaJob.original_video`, apesar do nome, e um derivado mutavel do processamento.
Ele e substituido depois de background voice, speech edit e final master. A funcao
`replace_file_safely` grava o novo objeto, atualiza o banco e so entao apaga o anterior,
o que reduz risco de perda. Ainda assim, o nome `original_video` confunde source com
working master e dificulta provenance.

Recomendacao: nao migrar arquivos agora. Introduzir `source_manifest` e tratar o campo do
Job como `working_master` conceitual; uma futura migracao de nome pode ocorrer com
compatibilidade.

### 8.3 Idempotencia

Pontos positivos:

- lock por projeto e task id evitam execucoes simultaneas/obsoletas;
- `StorageService.save_asset` atualiza asset pela chave job/kind/language;
- preview de upload retorna `skipped` se a midia foi apagada;
- diretorios temporarios isolam execucoes;
- jobs de reprocessamento preservam historico intencionalmente.

Lacunas:

- nao ha checkpoint por capability;
- uma redelivery valida pode recriar um render job e repetir API/FFmpeg;
- analysis results nao possuem chave idempotente;
- review preview pode ser regenerado sem substituicao versionada;
- Premiere export apaga referencias anteriores antes de salvar as novas no objeto;
- uma falha depois de criar `SubtitleVideoVersion`, mas antes de finalizar o projeto,
  pode exigir reconciliacao manual ou duplicar tentativa.

### 8.4 Versionamento e provenance

Ja existe:

- `MediaTemplateVersion` congelada por projeto;
- `SubtitleTrack.revision`, `SubtitleRevision` e snapshots;
- `SubtitleVideoVersion` associando revisions ao resultado;
- historico de `ExternalMediaJob` por projeto;
- schema `connect.internal_timeline.v1` no pacote;
- `AUTO_REFRAME_PLAN_VERSION` para invalidar planos antigos.

Falta:

- Timeline revision persistida e ligada a cada video final;
- versao/config/hash de transcription, silence, filler, diarization e audio analysis;
- provenance de cada cut com confidence e producer;
- snapshot explicito da configuracao efetiva do template por processing run;
- prioridade `AUTO`, `MANUAL`, `LOCKED` para overrides futuros.

### 8.5 Observabilidade e performance

`timed_step` registra inicio/fim e duracao de algumas fases. Projeto, Job e Step guardam
status e timestamps. Audio/QC armazenam metricas no JSON de configuracao.

Faltam correlation fields consistentes por run/capability, metricas persistidas por
etapa e contadores de cache/retry. O log de FFmpeg reduz stderr para 4.000 caracteres e
retorna erro amigavel, mas nao registra comando sanitizado, duracao, exit code e recurso
consumido de forma estruturada.

Operacoes redundantes identificadas:

- probe de duracao/dimensao/audio em varios services;
- extracao de audio para transcricao, fala, diarizacao, QC e mastering;
- primeira montagem em proxy e nova montagem do original (intencional para performance,
  mas exige manifests para evitar drift);
- novo encode completo ao queimar legendas;
- export Premiere copia todos os sources, extrai WAVs e renderiza overlay alpha mesmo
  quando um pacote semelhante ja existe;
- transcricao/diarizacao/reframe repetidos em reprocessamento sem mudanca de source.

Tambem existem boas otimizacoes: proxy de baixa resolucao, reuso do plano de reframe no
original, LUT preinterpolada, concat sem segundo encode, dois workers limitados para
normalizacao final e video copy nas fases apenas de audio.

### 8.6 Seguranca e isolamento

Pontos positivos:

- FFmpeg recebe argv, nao shell; entrada do usuario nao vira comando de shell;
- uploads usam paths gerados e diretorios temporarios por execucao;
- tamanho e extensao sao validados;
- downloads internos passam por views protegidas;
- revisao publica usa token longo armazenado como hash, expiracao e revogacao;
- paths do pacote Premiere sao validados para nao escapar do ZIP.

Pontos a decidir/corrigir:

- extensao e tamanho nao comprovam que o upload e midia valida; fazer probe cedo;
- views buscam projeto por UUID e exigem acesso ao modulo, mas nao filtram por criador.
  Confirmar se todo membro do ministerio deve enxergar todos os projetos; se nao, falta
  isolamento por ownership/equipe;
- argumentos extras de FFmpeg sao restritos pelo formulario admin, mas valores legados
  customizados continuam aceitos; manter essa superficie apenas para administradores;
- links publicos de revisao aceitam POST com `Origin: null` por desenho e dependem do
  segredo do token; rate limit e auditoria de tentativas seriam defesas adicionais.

## 9. Quick Wins

Classificacao: `NECESSARY NOW`.

1. Criar testes de paridade para custom blocks, `block_order`, background voice, intro,
   outro, LUT parcial e musica antes de alterar o pipeline.
2. Centralizar a resolucao efetiva de capabilities em uma unica funcao usada por render,
   progresso, Timeline e UI.
3. Introduzir um wrapper tipado para `Project.configuration` com schema/version e metodos
   para ler/gravar planos, sem migrar os dados de uma vez.
4. Extrair a combinacao/normalizacao de todos os cortes para uma funcao pura e fazer
   render e Timeline consumirem seu resultado.
5. Persistir um `source_manifest` por execucao com source id, nome, trim, ordem, bloco e
   duracao. Isso elimina duas descobertas independentes de sources.
6. Registrar `started_at`, `finished_at`, duracao, versao e resultado resumido em cada
   `ProjectPipelineStep`.
7. Fazer probe do upload apos o envio e rejeitar cedo arquivos sem video/audio valido.
8. Cachear probe e audio de analise dentro da mesma execucao antes de criar cache global.

Classificacao: `USEFUL SOON`.

9. Separar `services.py` por responsabilidade fisica, preservando APIs e testes.
10. Criar `AnalysisArtifact` inicialmente apenas para transcription, diarization e
    reframe plan.
11. Criar um `EditPlan` simples e serializavel produzido a partir do template.
12. Adicionar checkpoint por capability ao mesmo worker Celery.

## 10. Proposed Target Architecture

### 10.1 Estrutura recomendada

```text
Project + immutable TemplateVersion
              |
              v
       EditPlanCompiler
              |
              v
      EditingOrchestrator
              |
     +--------+---------+
     |                  |
 AnalysisCapabilities   |
     |                  |
 AnalysisArtifacts      |
     |                  |
     +--> Decision Policies
              |
              v
        EditDecisionSet
              |
              v
       InternalTimeline revision
              |
       +------+------+------+
       |             |      |
     Preview       Render  PremiereExport
```

Continuaria sendo um monolito Django com worker Celery.

### 10.2 EditPlan

Recomendado como dataclass/schema simples, nao como ORM inicialmente:

```json
{
  "schema": "connect.edit_plan.v1",
  "capabilities": [
    {"code": "assembly", "config": {}},
    {"code": "transcription", "config": {"language": "pt"}},
    {"code": "silence_removal", "config": {"profile": "balanced"}},
    {"code": "subtitle", "config": {"languages": ["pt", "en"]}},
    {"code": "music_mix", "config": {"profile": "default"}},
    {"code": "render", "config": {"preset_id": 1}}
  ]
}
```

Problema resolvido: hoje o plano esta implícito em models, assets, booleans e chamadas.
Complexidade adicionada: pequena, se for apenas dados validados. Necessidade: USEFUL SOON.

### 10.3 Capability Registry

Recomendado um registry estatico simples, sem discovery/plugins dinamicos:

```python
CAPABILITIES = {
    "translation": {"requires": ["transcription"], "resource": "CPU"},
    "filler_removal": {"requires": ["transcription"], "resource": "CPU"},
    "auto_reframe": {"requires": ["video_probe"], "resource": "CPU"},
    "mastering": {"requires": ["final_mix"], "resource": "CPU"},
}
```

Problema resolvido: dependencias e ordem hoje sao implicitas. Complexidade: baixa.
Necessidade: USEFUL SOON. Nao criar entry points, hot loading ou pacote de plugins.

### 10.4 AnalysisArtifact

Modelo recomendado quando a paridade da Timeline estiver estabilizada:

```text
AnalysisArtifact
  source_asset/source_hash
  type
  analyzer
  analyzer_version
  configuration_hash
  result_json/file
  status
  created_at
```

Chave de reuso:

```text
source content hash + analyzer version + normalized configuration hash
```

Comecar com reuso dentro do mesmo projeto. Reuso entre projetos pode vir depois, pois
exige ownership, retencao e seguranca mais cuidadosos.

### 10.5 EditOperation

Nao criar uma hierarquia de classes agora. Primeiro usar operacoes de dados dentro da
Timeline:

```json
{
  "id": "op-123",
  "type": "remove_segment",
  "source_id": "asset-1",
  "source_in_ms": 12000,
  "source_out_ms": 14600,
  "origin": "AUTO",
  "producer": "silence_detector",
  "producer_version": "2",
  "reason": "long_pause",
  "confidence": 0.98,
  "status": "ACTIVE"
}
```

Classes Strategy devem aparecer apenas quando houver implementacoes intercambiaveis.

### 10.6 Adapters

- manter `FFmpegRunner` como adapter concreto; nao criar `VideoRenderer` abstrato sem um
  segundo backend;
- extrair contrato de `TranscriptionProvider`, pois provider externo ja e uma fronteira
  real e o resultado deve ser testavel;
- manter `CommunitySpeakerDiarizer` como adapter;
- manter `PremiereExporter` como adapter de exportacao; criar interface apenas quando
  DaVinci/Final Cut se tornar demanda concreta.

### 10.7 CPU/GPU awareness

Adicionar apenas metadata `resource_class` no registry. Nao criar fila GPU agora. Quando
tracking/segmentacao justificarem GPU, a mesma capability podera ser roteada a outra
queue sem mudar EditPlan ou Timeline.

## 11. What NOT to Refactor

Classificacao: `NOT WORTH IT` agora.

- Nao dividir em microservicos.
- Nao substituir Celery por um workflow engine.
- Nao criar um plugin framework dinamico.
- Nao criar uma classe Python para cada operacao simples.
- Nao apagar `ExternalMediaJob`/`ExternalMediaProject` numa grande migracao.
- Nao reescrever SubtitleService: conteudo, timing, estilo e render ja possuem modelos e
  limites razoaveis.
- Nao abstrair FFmpeg para um backend imaginario.
- Nao trocar os services de audio, que ja estao bem separados.
- Nao remover o modelo de blocos; ele continua util como uma forma de construir uma
  track principal para varios templates.
- Nao tornar cada capability uma task Celery neste momento; checkpoints internos ja
  oferecem o beneficio principal com menos complexidade.

## 12. Migration Plan

### Fase 0 - Baseline e paridade (`NECESSARY NOW`)

- congelar fixtures representativas de Anuncio Mensal;
- adicionar projetos com custom block, reorder, intro/outro, bloco intacto e background
  voice;
- comparar source manifest, duracao, cuts, captions e ordem entre render e export;
- documentar output atual esperado.

Rollback: apenas testes/documentacao, sem alteracao funcional.

### Fase 1 - Decisoes e sources canonicos (`NECESSARY NOW`)

- criar schemas versionados `SourceManifest` e `EditDecisionSet`;
- envolver as chaves atuais de `Project.configuration` sem migracao destrutiva;
- centralizar resolucao de capabilities;
- centralizar normalizacao de cuts;
- fazer Timeline usar os manifests.

Rollback: manter leitura das chaves legadas e escrever ambos os formatos durante uma
janela de compatibilidade.

### Fase 2 - Timeline revision persistida (`USEFUL SOON`)

- gerar Timeline apos as decisoes, antes do render;
- persistir schema/revision/config/template/job ids;
- registrar qual Timeline gerou cada asset;
- fazer Premiere consumir essa revisao, sem reconstruir a edicao.

Rollback: manter exporter legado selecionavel por feature flag.

### Fase 3 - Render orientado pela Timeline (`USEFUL SOON`, alto risco)

- implementar executor apenas para clips/cuts/order primeiro;
- comparar frame count, duracao e audio sync com o render atual;
- migrar reframe, captions, LUT e audio uma capability por vez;
- manter o pipeline atual como fallback ate paridade comprovada.

Rollback: feature flag por template/projeto para executor legado.

### Fase 4 - AnalysisArtifact e cache (`USEFUL SOON`)

- transcription;
- speaker map;
- reframe plan;
- audio analysis;
- invalidacao por hash/version/config.

Rollback: ignorar artifact e reanalisar; artifacts sao append-only.

### Fase 5 - Orchestrator + EditPlan (`USEFUL SOON`)

- compilar TemplateVersion para EditPlan;
- validar dependencias com registry estatico;
- executar capabilities com checkpoints;
- derivar progresso das capabilities ativas.

Rollback: compilar plano equivalente ao pipeline legado e manter chamada antiga.

### Fase 6 - Primeiro novo formato (`FUTURE`)

Implementar Podcast simples como prova de composicao. Depois adicionar separadamente:

- `RetakeDetection` -> candidates;
- `RetakePolicy` -> operations;
- `MultiCamSync`;
- `CameraSelection`.

So entao avaliar Reel dinamico, B-roll e derived outputs.

## 13. Tests Required

### Antes de refatorar

- golden structural tests do source manifest e Timeline;
- custom blocks e ordem intercalada com template blocks;
- intro/outro efetivos no render e export;
- combinacao de background voice + silence/filler sem corte duplo;
- block intact sem LUT/reframe/dialogue/caption;
- trim com FPS fracionario e timestamps no original/proxy;
- paridade de duracao proxy/original/render/timeline;
- audio/video sync apos muitos cortes;
- LUT parcial;
- musica curta com crossfade no final;
- subtitle timing apos cortes e revisoes;
- task redelivery em cada ponto de persistencia;
- export apos reprocessamento e apos revisao de legenda.

### Durante a migracao

- contrato de cada capability: input, output e invariantes;
- dependency validation do EditPlan;
- invalidacao de AnalysisArtifact;
- prioridade `MANUAL > AUTO`;
- reproduzibilidade: mesma Timeline/config/source gera mesma estrutura;
- executor legado e novo produzem mesma duracao, ordem e ranges;
- failure injection no storage e FFmpeg;
- compatibilidade de schema antigo.

### Cobertura atual avaliada

A suite atual e forte em comportamento local: timestamps, ASS, traducao, revisao,
silencio/filler, background voice, reframe, montagem, audio, mastering, Timeline e XML.
Os maiores vazios sao testes de integracao entre subsistemas e paridade render/export,
especialmente custom blocks, ordem global e planos combinados.

## 14. Risks

### Risco alto: tornar Timeline SSOT de uma vez

Dependencias: cuts, reframe, captions, audio, protected ranges e export. Pode alterar
timing e sync. Mitigacao: migrar uma operacao por vez, comparacao estrutural e feature
flag. Rollback: executor legado.

### Risco alto: unificar Project e Job

Ha historico de processamentos, revisoes e assets dependentes. Mitigacao: nao fazer agora;
primeiro introduzir `ProcessingRun` conceitual sobre o Job existente.

### Risco alto: operation-per-row no banco

Pode gerar milhares de rows/cues/keyframes e transacoes caras sem beneficio imediato.
Mitigacao: JSON schema revisionado primeiro; normalizar em tabelas apenas quando queries
ou edicao colaborativa exigirem.

### Risco medio: cache de analise incorreto

Um hash/config incompleto reutilizaria resultado invalido. Mitigacao: source content hash,
analyzer version e configuracao normalizada obrigatorios; artifacts append-only.

### Risco medio: alterar plugin resolution

Templates atuais podem depender do comportamento implicito. Mitigacao: snapshot dos
templates atuais, testes e migracao explicita de flags antes da correcao.

### Risco medio: tarefas por capability

Mais mensagens, estados intermediarios e compensacao. Mitigacao: checkpoints no mesmo
worker primeiro.

## 15. Final Recommendation

Ordem recomendada:

1. Corrigir por testes a paridade entre render e exportacao.
2. Centralizar a resolucao efetiva de capabilities.
3. Versionar e tipar source manifest + edit decisions dentro do modelo atual.
4. Persistir uma Timeline revisionada antes do render.
5. Fazer Premiere consumir essa revisao.
6. Migrar o render para a Timeline por capability, mantendo fallback.
7. Introduzir AnalysisArtifact para analises realmente caras.
8. Adicionar EditPlan/registry simples e checkpoints.
9. Validar a arquitetura com Podcast simples.
10. Implementar multicamera, retakes e B-roll como capacidades independentes.

A direcao recomendada nao e criar cinco editores. E preservar o que ja esta generico,
estabilizar a representacao da edicao e transformar o template em uma composicao
declarativa sobre esse nucleo.

```text
Media sources imutaveis
        -> AnalysisArtifacts
        -> EditDecisionSet (AUTO/MANUAL + provenance)
        -> InternalTimeline revision
        -> Preview / Render / Premiere
```

Essa evolucao resolve problemas ja presentes no codigo, reduz divergencias e prepara
Podcast/Reel/Testemunho sem impor uma arquitetura distribuida ou cerimonial antes da
necessidade.
