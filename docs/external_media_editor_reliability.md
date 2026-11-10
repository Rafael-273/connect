# Confiabilidade do editor: primeira implementação

Continuação: [segunda etapa — fluidez (P1)](external_media_editor_fluency.md).

## Escopo

Implementa a etapa P0 da auditoria e o agendamento por frame de drag, resize e
scrubbing (P1). Não muda o framework, os formatos das timelines existentes nem o
processamento audiovisual do renderizador. Snapping e os demais refinamentos P2
continuam fora desta etapa.

## Fluxo de edição

- `static/external_media_editor_state.js` coordena uma requisição editorial por
  vez, debounces independentes do DOM, gestos ativos, retry e confirmação.
- A timeline local é atualizada ao concluir um movimento e ao editar propriedades.
  O movimento transitório continua no elemento capturado durante o gesto.
- Respostas são aplicadas quando não há gestos, timers ou operações pendentes.
  Enquanto isso, somente o snapshot confirmado mais recente é retido. Não há uma
  segunda cópia completa da timeline local para cada edição.
- Patches adjacentes do mesmo alvo, ainda não enviados, são agrupados. Nunca se
  modifica o payload de uma operação enviada, inclusive após erro de rede.
- Undo/redo e aprovação drenam os debounces e a fila antes de executar. Durante
  essa barreira, novas interações editoriais ficam temporariamente indisponíveis.
- O indicador no cabeçalho permite retry e exportação das pendências. Recarregar
  a versão salva exige descartar explicitamente as alterações locais. A página
  solicita confirmação de saída enquanto há trabalho pendente.

`frameLatest` limita os handlers de movimento a uma execução por frame e aplica
a última amostra antes de finalizar o gesto. A geometria da pista e o centro do
resize são fixados no início da interação.

Os inspetores de overlay/B-roll enviam somente os campos modificados. Objetos
como `position`, `transform` e `content` são mesclados no backend. A conversão de
minutos não arredonda mais os valores para múltiplos de 60 ms.

## Protocolo e persistência

O editor envia `X-Editor-Operation` (UUID) e `X-Editor-Version` em todas as mutações,
inclusive uploads. `PreviewMutationMixin` executa, em uma única transação:

1. Bloqueio do projeto com `select_for_update`.
2. Consulta do recibo da operação. Uma repetição idêntica retorna o resultado já
   confirmado, mesmo que sua versão base tenha ficado antiga.
3. Validação da versão esperada e do estado de edição do projeto.
4. Execução da view existente e gravação do recibo se a resposta for bem-sucedida.

`editorial_version` cresce ao criar revisão, navegar no histórico, reabrir ou
reprocessar. É independente da numeração editorial, que pode diminuir em um undo.
Uma operação obsoleta recebe HTTP 409 antes de modificar dados. A fila pausa e
preserva as alterações; não força automaticamente uma sobrescrita sobre outra
sessão.

`PreviewOperation` registra projeto, membro, UUID, hash da requisição e resposta.
Hashes de multipart consideram campos e conteúdo dos arquivos, não o boundary
HTTP. O recibo e a revisão são confirmados juntos. Uma resposta perdida após
commit pode ser recuperada sem duplicar revisão, corte ou elemento.

Clientes antigos sem esses headers mantêm compatibilidade com as rotas atuais;
eles não têm a garantia de comparação de versão do protocolo novo. Ambos os
caminhos recusam mutações do preview durante processamento/renderização. Os
bloqueios e patches existentes nos serviços continuam sendo utilizados.

## Ativação

Aplicar a migration `0118_editor_persistence` e publicar os arquivos estáticos
com o processo normal de deploy:

```sh
python manage.py migrate
python manage.py collectstatic --noinput
```

Nesta implementação, migrations foram aplicadas somente em PostgreSQL temporário
de teste. O banco configurado pelo projeto e o ambiente publicado não foram
modificados. O módulo novo precisa ser servido junto com o template atualizado.

## Testes reproduzíveis

```sh
node --test static/tests/external_media_editor_state.test.cjs
python manage.py test website.test_editor_persistence
```

O teste concorrente usa duas conexões reais e requer PostgreSQL. Verifica conflito
no mesmo elemento e em elementos diferentes. A suíte cobre também confirmação
sequencial, retry após commit, UUID reutilizado, rejeição sem efeitos parciais,
undo, aprovação e bloqueio durante render.

Os testes JavaScript cobrem agrupamento, ordenação, operações pendentes,
respostas antigas, gestos ativos, perda de resposta, retry, conflito, barreira de
aprovação e entrega da última posição no frame final. Executam também a função
`applyTimeline` extraída do template.

Validação realizada em 21/10/2026:

- 16 testes JavaScript aprovados.
- 11 testes Django novos aprovados em PostgreSQL isolado.
- 7 regressões direcionadas do editor aprovadas.
- Classe existente `ExternalMediaProjectTests`: 76 de 79 aprovados. As três falhas
  abaixo foram reproduzidas também no HEAD original, em checkout e banco de teste
  separados, sem estas alterações:
  - `test_finished_project_can_queue_editable_premiere_export`;
  - `test_finished_project_has_no_remaining_estimate_or_pending_eta_copy`;
  - `test_review_link_is_hashed_and_public_page_exposes_only_allowed_tracks`.
- `manage.py check` e `makemigrations --check --dry-run` sem problemas.

QA no Chrome com uma fixture descartável e respostas atrasadas entre 500 ms e
2 s confirmou: duas legendas redimensionadas consecutivamente, alterações
repetidas na mesma legenda, propriedades e movimento de overlay com salvamento
pendente, resposta perdida depois do commit, retry e undo. Os valores foram
conferidos após reload; não houve exceções JavaScript. Foram observadas oito
tentativas HTTP para sete operações confirmadas, com máximo de uma mutação em voo.

## Métricas e limites

O evento `connect:editor-save` emite `operationId`, `queueMs`, `requestMs`,
`totalMs` e `attempts`. Permite separar espera na fila de latência HTTP e contar
tentativas por operação.

Na sessão sintética acima, o maior tempo HTTP foi 2066 ms, o maior tempo total
foi 2879 ms e o p95 entre pointermove e o próximo callback de animation frame
foi 28,9 ms. Este último é uma aproximação de agendamento visual, não uma medida
da pintura ou decodificação de vídeo. A fixture não valida playback nem render
FFmpeg; não representa um benchmark de um projeto grande.

Pendências permanecem em memória enquanto a aba está aberta; exportá-las gera
JSON para recuperação manual. Não foi implementado um journal durável nem
importação automática. No caso de upload, esse JSON contém metadados do arquivo,
não seus bytes. Um conflito entre sessões exige reconciliação explícita, sem
sobrescrita automática. Recibos são mantidos pelo projeto e removidos com ele;
uma política de retenção pode ser acrescentada se o volume justificar.
