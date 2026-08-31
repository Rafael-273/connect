# Preview interativo da InternalTimeline

O preview do ConnectCut é uma interpretação da `InternalTimeline`, não um segundo motor editorial.
Ele nunca decide cortes, câmera, layout, cor ou áudio. Essas decisões chegam pelo
`EditDecisionSet` e são preservadas em `TimelineRevision`.

## Contrato

- `SourceManifest` identifica cada fonte original e seus limites temporais.
- `ProjectSourceProxy` associa a fonte a um `ProxyProfile`, mantendo o mesmo eixo de tempo.
- `EditDecisionSet` contém operações identificáveis, origem, produtor, motivo e confiança.
- `PreviewCompositionService` combina fontes, decisões e legendas sem criar regras editoriais.
- `TimelineRevision` é imutável e registra a timeline completa usada na revisão.
- `PreviewSession` mantém posição, filtros e pilhas de undo/redo por membro.
- `TimelineMutation` registra a operação e sua inversa para auditoria.
- `approved_timeline_revision` é a única revisão aceita pela renderização e pela exportação.

## Fidelidade

Cada capacidade declara sua fidelidade no payload do preview:

- `EXACT`: cortes e legendas, aplicados na mesma base temporal do render.
- `APPROXIMATE`: transformações, cor e automação de áudio no navegador.
- `NOT_AVAILABLE`: capacidades ainda sem representação visual no browser.

O proxy `community-1` reduz resolução e bitrate, mas não altera a velocidade ou a base
temporal. Uploads reutilizam o proxy já preparado; fontes fixas do template ganham um proxy
catalogado por projeto e fonte.

## Ciclo de revisão

1. A análise persiste manifesto e decisões canônicas.
2. O worker prepara/reutiliza proxies e cria a revisão inicial.
3. O projeto entra em `AWAITING_REVIEW`.
4. Cada edição cria uma revisão e marca o resultado final como desatualizado.
5. Restaurar um corte remapeia também as legendas posteriores.
6. Undo/redo troca a revisão ativa e restaura as legendas daquele snapshot.
7. Aprovar fixa a revisão e enfileira a renderização.
8. Render e exportação registram o número exato da revisão consumida.

Novas capacidades devem ser adicionadas ao `PreviewCapabilityRegistry` e ao compositor.
Regras específicas de um formato ou template não pertencem a essa camada.
