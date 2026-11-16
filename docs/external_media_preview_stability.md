# Estabilidade do preview — primeira rodada

Implementação local em 16/11/2026, após a [auditoria de performance](external_media_performance_audit.md). Nenhum deploy, migration ou alteração de dados reais foi executado.

## Mudanças

### Carregamentos com saída e retry explícito

`static/external_media_preview_source.js` acompanha progresso durante a reserva do próximo decoder e o carregamento frio. Sem mudança útil de prontidão/buffer por 5 segundos, ou após 30 segundos totais nesse carregamento, encerra a espera e informa erro recuperável. Os limites são opções do construtor, permitindo ajustes depois de medir a rede/dispositivo. Eventos repetidos sem mudança do estado observado não renovam o prazo.

Falha invalida as gerações de callbacks, aborta listeners temporários, cancela o monitor, pausa os dois players e revoga autoplay. A página mostra “Tentar novamente”; esse botão ou um novo comando de play repete o carregamento da posição solicitada. Dados e eventos tardios não retomam o pedido falho. Um novo seek válido limpa o erro. Pausar uma transição reservada cancela seu monitor; carregamento frio pendente ainda pode concluir pausado ou apresentar erro.

Esse monitor cobre carregamento/reserva, não representa uma recuperação geral de todo congelamento durante reprodução contínua. O watchdog de áudio existente permanece separado.

### B-rolls com janela limitada

`renderBrolls` preserva todos os itens ativos, incluindo camadas simultâneas, e até dois vídeos adicionais próximos do playhead. O índice de recursos consulta intervalos de 3 segundos antes do início até 1 segundo depois do fim; a seleção favorece proximidade ao início do item.

Ao sair dessa janela/orçamento, o vídeo recebe `pause()`, retirada de `src`, `load()` e remoção do nó. Os listeners próprios são abortados. A mesma limpeza ocorre quando o proxy contínuo assume a composição, um item é excluído ou muda de vídeo para imagem. Vídeos próximos começam a carregar antecipadamente. Metadados de um B-roll ativo agendam a atualização do preview, inclusive quando o principal está pausado.

O limite é de elementos/fontes, não de bytes; `preload` continua sendo uma indicação ao navegador. Camadas simultâneas não são descartadas para cumprir um teto arbitrário. Não há alteração editorial, de áudio original, nem do contrato da timeline.

### Câmera pronta no tempo zero

`static/external_media_multicam.js` reconhece um frame pronto na posição atual sem depender exclusivamente de `seeked`. No preview pausado, utiliza tolerância de um frame; se o destino mudar durante um seek, reavalia a posição mais recente quando o decoder termina. Segmentos com a mesma URL reutilizam a mídia já carregada. Eventos tardios sem prontidão não revelam uma fonte recém-substituída.

### Métricas opcionais

Abrir o editor com `?preview_metrics=1` (ou `&preview_metrics=1` se já houver query string). O módulo `static/external_media_preview_metrics.js` fica inativo quando essa opção não está presente. Não envia dados para um serviço.

No console do navegador:

```js
const result = window.connectPreviewMetrics.export();
console.table(result.samples.filter(sample => sample.type === 'seek'));
console.table(result.samples.filter(sample => sample.type === 'transition'));
console.table(result.samples.filter(sample => sample.type === 'resources'));
```

O histórico retém no máximo 300 amostras. Recursos são amostrados no máximo uma vez por segundo durante frames, além da exportação explícita. Incluem vídeos do stage, canais musicais fora do DOM, amostras de música e áudios de comparação presentes no inspetor.

- `seek`: duração, destino, ID e resultado. Pedidos substituídos, falhos e expirados não contam como sucesso. `presented/video-frame` usa o callback nativo recebido pelo relógio; `ready/ready-after-raf` é uma observação mais fraca de prontidão, útil quando pausado, e deve ser analisada separadamente.
- `transition`: preserva os eventos existentes de warm/waiting/cold/swap/first-frame/error, inclusive gap excedente dos callbacks. O callback não mede fisicamente a exibição dos pixels.
- `resources`: total de elementos, vídeos, fontes atribuídas, players não pausados e vídeos explicitamente ocultos ou com opacidade zero que conservam fonte. Não infere oclusão de camadas, bytes de memória ou número real de decoders.

A instrumentação mede o caminho principal de seek; não comprova sincronismo/pintura de todas as camadas. Play/pause latency, long tasks e memória por processo ainda não receberam uma coleta completa nesta rodada.

## Validação local

Node v24.14.0 e Chrome 141.0.7390.65/Linux headless, com mídias WebM sintéticas. Playwright instalado somente em `/tmp/connect-performance-audit`, fora das dependências do produto.

```sh
node --test static/tests/*.test.cjs

FILADELFIA_PLAYWRIGHT_MODULE=/tmp/connect-performance-audit/node_modules/playwright-core \
FILADELFIA_CHROME_PATH=/usr/bin/google-chrome \
node static/tests/editor_playback_continuity.browser.cjs

FILADELFIA_PLAYWRIGHT_MODULE=/tmp/connect-performance-audit/node_modules/playwright-core \
FILADELFIA_CHROME_PATH=/usr/bin/google-chrome \
node static/tests/editor_preview_resources.browser.cjs
```

Resultados:

- **174 testes JavaScript aprovados**, incluindo timeout de carregamento frio/reservado, prazo absoluto, pausa, callbacks antigos, retry no destino mais recente, câmera pronta sem seek, reuso entre segmentos e diagnósticos limitados.
- Continuidade com decoder real aprovada após corrigir os helpers `playbackIn/playbackOut` ausentes no harness. Cobre corte descontínuo no mesmo arquivo, troca de duas fontes, música contínua nos swaps preparados e reserva de fonte atrasada.
- Na execução registrada de continuidade, os gaps excedentes dos callbacks foram `[67,83,67]` ms antes e depois das mudanças iniciais. São amostras de uma fixture pequena, não percentis nem evidência de melhoria de latência. Esta rodada corrige estabilidade/recursos, sem afirmar redução desses gaps.
- Varreduras de **50 e 100 itens sequenciais de B-roll**: pico de 2 vídeos com fonte atribuída; zero após sair dos intervalos. A fixture usa o mesmo recurso local e exercita ciclo de vida/DOM; não é um benchmark de reprodução de 100 cortes do principal nem de 100 arquivos distintos.
- Quatro B-rolls simultâneos preservados; descarregamento ao assumir proxy contínuo e ao substituir vídeo por imagem aprovado; offset inicial pausado testado em 500 ms.
- Câmera nativa em zero e seek pausado de 100 ms aprovados.
- Fonte futura bloqueada: erro após limite configurado do ensaio, loading encerrado, dados tardios sem autoplay, retry pausado bem-sucedido. As métricas distinguem erro e prontidão pausada.
- JavaScript do template validado sintaticamente após substituir placeholders Django; `git diff --check` aprovado.

O teste de recursos executa funções originais extraídas do template e módulos originais em uma página local controlada. A suíte completa Django e a interface integral com autenticação/backend não foram executadas nesta rodada. Não foram medidos MB de memória, Safari/iOS/Android, H.264/GOP, S3 ou sessão real de 30 minutos. Esses ensaios permanecem necessários antes de declarar estabilidade em todos os ambientes.

## Próxima rodada

Continuam pendentes a coordenação geral de seeks latest-wins, precisão final e loop de B-roll, custo global de render da timeline, trabalho por frame e perfil do backend. A tolerância contínua de 350 ms de B-roll foi preservada nesta rodada; scrubs pequenos dessa camada ainda precisam da segunda etapa proposta na auditoria.

Os arquivos da auditoria original e seus resultados são históricos. O script `docs/performance_audit/browser_probe.cjs` lê o commit-base com `git show`, para continuar reproduzindo os problemas originais após essas correções. As regressões da implementação atual estão em `static/tests/`.

Para publicação futura, o template e os scripts atualizados, incluindo `external_media_preview_metrics.js`, devem seguir juntos pelo fluxo normal de estáticos. A publicação não foi realizada nesta execução.
