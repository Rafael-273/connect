# Editor Filadélfia — segunda etapa (P1)

Esta etapa mantém o protocolo, a fila e as revisões do P0. Não modifica decisões
editoriais, templates, modelos de banco ou renderização FFmpeg.

## Mudanças

- **DOM estável:** pistas e clips são reconciliados por identidade. Seleção
  atualiza classes; zoom altera a largura do canvas. O playhead e os canvases
  existentes são preservados. Os handlers leem o descritor atual ao iniciar o
  gesto, inclusive após mover um elemento para outra camada.
- **Scrubbing:** o vídeo pausa durante o gesto e só retoma ao soltar, quando já
  estava tocando. Metadados, preloader e callbacks de fontes anteriores não
  podem recuperar um seek antigo. Carregar um decoder não reconstrói a timeline.
- **Reprodução:** o preview acompanha `requestVideoFrameCallback`, com fallback
  para animation frames. O relógio é cancelado ao pausar ou trocar o decoder.
  `timeupdate` continua disponível para sessão e atualizações quando pausado.
- **Preview:** índices derivados buscam apenas os intervalos ativos de legendas,
  overlays, B-roll e transcrição. Referenciam os objetos canônicos e são
  invalidados por mudanças locais de tempo ou por snapshots confirmados. Estilos
  de legenda são reaplicados quando texto, estilo, sequência ou geometria mudam.
- **Áudio:** camas de fala e keyframes são preparados uma vez e consultados por
  busca binária. Edições invalidam os caches; attack, hold, release, interpolação
  e precedência permanecem iguais aos cálculos anteriores.
- **Formas de onda:** somente clips visíveis iniciam seu carregamento. A extração
  de picos cede tempo à interface; cada frame desenha no máximo dois canvases.
  A alocação acompanha a janela visível, com pequena margem, em vez de toda a
  largura do clip em zoom elevado. Observers de elementos removidos são limpos.
- **Inspetor e navegação:** atualizações preservam foco e seleção de texto no
  mesmo campo. Geometria de drag considera scroll horizontal sem reler layout a
  cada movimento. Resize da janela recalcula o zoom, sem reconstruir clips.

`static/external_media_editor_view.js` contém apenas estruturas derivadas para a
interface. A timeline continua sendo a fonte dos valores editoriais; não foi
criado um segundo estado editável. O novo módulo deve ser publicado junto com
os scripts de áudio/source e o template atualizado.

## Testes

```sh
node --test static/tests/*.test.cjs
python manage.py test website.test_editor_persistence
```

Os testes de navegador são opcionais e usam `StaticLiveServerTestCase` com
fixtures descartáveis no banco de testes. Não recebem URLs ou cookies de
projetos reais. Para executá-los no macOS com Chrome instalado:

```sh
npm install --prefix /tmp/filadelfia-browser-tests playwright-core
FILADELFIA_PLAYWRIGHT_MODULE=/tmp/filadelfia-browser-tests/node_modules/playwright-core \
FILADELFIA_CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
python manage.py test website.test_editor_browser
```

Sem `FILADELFIA_PLAYWRIGHT_MODULE`, esses dois testes são ignorados. A fixture de
mídia gera WebM com vídeo e áudio dentro do Chrome, sem depender de FFmpeg.

## Validação em 21/10/2026

- 33 testes JavaScript aprovados: persistência P0, consultas de intervalos,
  identidade dos nós, scroll durante drag, automação de áudio e eventos atrasados
  do player.
- 11 testes de persistência e 7 regressões direcionadas do Django aprovados em
  PostgreSQL isolado, com Python 3.11 e dependências de `requirements.txt`.
- Dois testes Chrome/Django aprovados: seleção/zoom preservando os clips e o
  playhead, restauração de foco/caret, scrubbing com 1.000 legendas e transcrição
  aberta, preview pausado atualizado antes do ACK de dois segundos, duas fontes
  de vídeo reais, reprodução e formas de onda decodificadas.
- Os cenários P0 de latência entre 500 ms e 2 s, edições consecutivas,
  propriedades/movimento de overlay, resposta perdida, retry, undo e reload
  continuaram aprovados no Chrome.

Na amostra com 1.000 legendas, foram observados zero replacements de clips em
seleção/zoom e p95 de aproximadamente 2 ms entre pointermove e o próximo callback
de animation frame. Com vídeo e áudio, 26 de 28 leituras espaçadas em cerca de
40 ms capturaram posições distintas do playhead, incluindo a troca de take. Em
zoom 16×, os três canvases de waveform permaneceram abaixo de 1.600 pixels de
largura no viewport de 1.600 × 1.000, com devicePixelRatio 1.

Esses valores descrevem fixtures específicas, não garantem latência de pintura,
decode ou FPS para qualquer projeto. Não foi realizado um benchmark de render
final. As três falhas preexistentes da suíte ampla registradas no relatório P0
não foram alteradas nesta etapa.

## Ativação e próxima etapa

P1 não acrescenta migrations. A migration `0118_editor_persistence` do P0 ainda
é necessária se aquela etapa não foi ativada. Publicar os estáticos com o fluxo
normal de deploy (`collectstatic`). Nenhum banco real ou ambiente publicado foi
modificado nesta validação.

Snapping magnético, guias de alinhamento, timecode por frame e demais refinamentos
P2 permanecem como a terceira etapa.

A terceira etapa está documentada em
[Precisão e experiência profissional (P2)](external_media_editor_precision.md).
