# Representacao Canonica da Midia Externa

Esta e uma camada de compatibilidade da Fase 0 e Fase 1. O render legado
continua sendo o executor; os snapshots apenas descrevem a mesma edicao de
forma versionada e deterministica.

## SourceManifest

Schema: `connect.source_manifest.v1`.

Responde "o que temos?": uploads, videos padrao, blocos personalizados e
assets de abertura/encerramento que efetivamente participam do projeto. Cada
source tem uma identidade estavel por revisao (`project-media-<id>` ou
`template-default-<id>`), trim e posicao ja resolvida por `block_order`.

## EditDecisionSet

Schema: `connect.edit_decisions.v1`.

Responde "o que decidimos fazer?": remocoes de segmento, faixas protegidas,
reframe, cor/LUT e automacao de audio. Cada operacao informa origem, produtor,
versao, motivo e confianca quando disponivel.

## InternalTimeline

Schema: `connect.internal_timeline.v1`.

Responde "como isso fica no tempo?". A exportacao Premiere agora le o
manifest e o conjunto de decisoes, com fallback para monta-los a partir do
estado legado quando um projeto antigo ainda nao possui snapshots.

Os snapshots convivem temporariamente com `Project.configuration`.
Processamentos novos fazem dual-write; nenhuma migracao destrutiva de projetos
historicos e executada. A remocao desta compatibilidade sera decidida na Fase
2, quando o executor consumir a timeline diretamente.

## Limites desta fase

O render FFmpeg continua usando seu fluxo legado. A timeline ainda e usada por
exportacao e analise, agora com a mesma resolucao de sources e cortes. Nao foi
introduzido orchestrator, sistema de plugins, migracao em massa, novos tipos de
midia nem executor baseado em timeline. O `ffprobe` da timeline possui cache
somente por execucao, sem cache distribuido ou artefatos persistentes.
