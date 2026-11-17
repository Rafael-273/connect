# Editor de vídeos — experiência mobile

## Auditoria e organização

O editor de montagem está em `templates/member/external_media/preview.html`.
`editor.html` é o editor independente de legendas de um processamento e não
contém a timeline de montagem. A timeline principal, o compositor, a seleção,
os comandos editoriais e a ligação com o histórico ficam no template de preview.
Os módulos `external_media_editor_state.js`, `external_media_editor_view.js`,
`external_media_editor_precision.js`, `external_media_editor_timeline.js` e o
controlador existente de reprodução fornecem persistência, DOM incremental,
encaixe, operações locais e reprodução com proxies/buffers.

A implementação anterior já usava Pointer Events, captura, RAF e cancelamento
na timeline. Os problemas mobile eram: altura ilimitada do preview vertical,
transporte deslocado pela regra desktop de largura total, clipes arrastáveis
competindo com rolagem, marquee de legendas interceptando toque, controles
pequenos, inspector lateral e ações dependentes de clique duplo.

O plano aplicado foi conservar todos esses componentes e adicionar apenas
apresentação responsiva e arbitragem de gestos. Não há um segundo estado,
player, framework, formato de projeto ou endpoint mobile.

## Uso no celular

- Cabeçalho compacto com retorno ao projeto, histórico, menu, aprovação e
  feedback do salvamento existente, incluindo recuperação de erros.
- Preview limitado pelas duas dimensões disponíveis, respeitando 16:9/9:16.
  No mobile a timeline reserva de 152 a 216px (reduzidos em paisagem), deixando
  o restante para o monitor. Em telefones altos cabem régua e três faixas sem
  reservar espaço vazio proporcional à tela; faixas adicionais continuam roláveis.
  **Mais → Ampliar preview** amplia o monitor; **Voltar à timeline**, no mesmo painel, restaura as faixas.
- Transporte compacto de 44px, com play/pause centralizado, avanço por quadro,
  posição/duração à esquerda e desfazer/refazer à direita. O zoom usa pinça. O play tem círculo visual de
  32px, mantendo área de toque de 44px; tempo atual e duração usam duas linhas
  discretas dentro da mesma faixa.
- **Navegar** é o estado inicial: arrastar sobre clipes ou espaços navega nos
  dois eixos; toque sem deslocamento seleciona. Rótulos permanecem fixos.
- **Mover** habilita os manipuladores existentes: centro move, bordas
  ajustam duração, B-roll pode trocar de camada, takes principais reordenam.
  O movimento de toque tem limiar de sete pixels para reduzir edições acidentais.
- **Pinça** funciona nos dois modos, limitada a 1–16×. Um segundo dedo cancela
  um arraste provisório antes do zoom, sem persistir esse movimento.
- Régua e cabeça da agulha fazem seek direto. A haste da agulha não bloqueia
  a seleção de clipes que estejam sob ela. O acompanhamento durante reprodução
  continua sendo o já existente.
- **Cortar** usa o corte existente no cursor: legenda, música, B-roll,
  overlay ou montagem principal, conforme a seleção. A lâmina continua acessível.
- **Ajustes** abre os mesmos formulários em painel inferior. **Editar texto**
  abre explicitamente o editor de legenda selecionada, sem exigir clique duplo.
- Uma única barra permanece abaixo da timeline, com ícone em cima e rótulo
  pequeno embaixo: Navegar/Mover, Ajustes, Editar texto (somente para legendas),
  Cortar, Excluir, Adicionar e Mais. A barra rola horizontalmente em telas pequenas.
- **Adicionar** abre uma folha inferior com imagem/vídeo, texto, música e câmera
  quando disponível. **Mais** reúne tempos, encaixe e ampliação do preview;
  copiar aparece para legenda selecionada e colar aparece quando há conteúdo copiado.
- Seleção e lâmina separadas não aparecem no mobile: toque já seleciona, o modo
  Mover controla arrastes e Cortar atua no cursor. Zoom continua disponível por pinça; controles duplicados e ajuda de teclado ficam fora do menu mobile.
  Os controles reais são realocados, preservando os handlers e os locais desktop.
- Controles principais têm 44px; as zonas de resize usam até 22px por borda.
  Clipes curtos mantêm a escala temporal e podem ser ampliados ou ajustados no
  painel de tempos. O encaixe usa tolerância de 14px para toque e 8px para mouse.
- Multifaixas, cores por tipo, composição de elementos simultâneos, propriedades
  visuais, volume, transições e ducking continuam usando as operações existentes.

## Responsividade e persistência

A apresentação de telefone entra até 760 CSS px. Tablets e desktop conservam
sua apresentação existente. A mudança de tamanho recalcula o preview sem
recriar o motor. A página fica contida na viewport; faixas, ferramentas,
formulários e modais têm suas próprias regiões roláveis. `dvh`, safe areas,
`viewport-fit=cover` e `visualViewport` acomodam barras e teclado. O painel
inferior acompanha o fundo da viewport visual, mesmo quando a viewport de
layout não encolhe. Campos usam 16px para evitar zoom automático no iOS.

Pan e pinça são agrupados por RAF, sem gravações editoriais. Os arrastes
continuam usando DOM local e a fila existente ao terminar o gesto. Os buffers
e proxies existentes permanecem em uso. `InternalTimeline`, revisão,
controle de versões, histórico, serialização e proteção contra respostas
antigas não foram alterados. Cancelar uma transformação no preview agora
restaura o valor anterior sem salvar, assim como cancelar um arraste na timeline.

## Arquivos

| Arquivo | Responsabilidade |
| --- | --- |
| `templates/member/external_media/preview.html` | Integração, controles contextuais, dimensionamento e comandos compartilhados |
| `static/external_media_editor_mobile.css` | Layout, toque, safe areas, folhas inferiores e orientação |
| `static/external_media_editor_mobile.js` | Arbitragem entre toque, pan, pinça e edição |
| `static/external_media_caption_selection.js` | Mantém marquee para mouse, liberando touch para navegação |
| `static/tests/editor_mobile.browser.cjs` | Gestos touch reais e persistência em fixture Django |
| `static/tests/editor_media.browser.cjs` | Mesma reprodução real em desktop/mobile; resposta JSON correta da fixture de waveform |
| `website/test_editor_browser.py` | Registro e execução dos testes mobile |
| `website/editor_browser_test_settings.py` | SQLite temporário, arquivos temporários, email local e fila em memória |
| `docs/external_media_editor_mobile.md` | Auditoria, decisões, validação e limites |

## Validação

- JavaScript: **174 testes passaram**.
- Django/persistência: **17 passaram, 1 ignorado**. O teste de concorrência
  exige PostgreSQL; SQLite não fornece os bloqueios necessários.
- Suíte integrada: **27 testes, 25 passaram, 1 falhou e 1 foi ignorado**;
  os oito cenários browser restantes passaram.
- Browser: seleção, pan horizontal/vertical, pinça, segundo dedo cancelando
  edição, movimento, resize, cancelamento sem POST, recarga, edição de texto,
  tempos por quadro, painel inferior, viewport visual reduzida, expansão,
  seek e corte de legenda são exercitados com touch confiável do Chromium/CDP.
- Viewports: 320×568, 390×844, 430×932, 740×360, 390×500, 820×1180 e 1440×900,
  incluindo monitor 9:16, verificação de overflow e acesso aos botões de desfazer e refazer.
- Reprodução de WebM real em desktop/mobile: play/pause, scrubbing entre duas
  fontes, atraso da próxima fonte, continuidade do relógio, áudio aguardando
  vídeo e pintura das formas de onda a 16×. Os picos usam o contrato JSON
  do cache; a reprodução usa bytes de vídeo/áudio reais gerados no navegador.
- Regressões desktop: precisão/snapping, undo/redo, visibilidade de legendas,
  exclusões otimistas, transições de música, foco/caret e mil legendas.
- **Falha preexistente:** `editor_framing.browser.cjs:62` recebe transformação
  vazia ao esperar `scale(1.35)` depois de salvar. Reproduzida separadamente com
  o template original do HEAD, sob a mesma configuração isolada. Esse teste
  permanece vermelho e impede afirmar que toda a suíte passou.
- `git diff --check` e verificações de sintaxe JavaScript sem erros.

Execução reproduzível, sem serviços de produção:

```sh
node --test static/tests/*.test.cjs
FILADELFIA_PLAYWRIGHT_MODULE=/caminho/node_modules/playwright-core \
FILADELFIA_CHROME_PATH=/caminho/chrome \
.venv/bin/python manage.py test \
  website.test_editor_browser website.test_editor_persistence \
  --settings=website.editor_browser_test_settings --noinput
```

## Limites e próximos passos

Não houve deploy, migração, alteração de configuração de produção ou operação
em armazenamento de usuários. A configuração de QA usa recursos descartáveis.

A validação touch automatizada usa Chromium, e não certifica Safari iOS nem
hardware Android. Validar nesses dispositivos notch, teclado real, gestos do
sistema, autoplay e desempenho de projetos extensos antes de publicar. O teste
de viewport visual reduzida modela o teclado; não abre um teclado de sistema.
Priorizar também a investigação da falha de enquadramento preexistente e o
reteste de concorrência em PostgreSQL descartável. O monitor vertical em
paisagem fica deliberadamente menor; a expansão permite inspecioná-lo melhor.
Não foram criados recursos que o desktop não possuía, como duplicação genérica
de takes ou resize do material principal além das operações já suportadas.

### Refinamento da barra inferior

Unificadas as duas áreas de ferramentas em uma barra abaixo das faixas. Teste
mobile ampliado para verificar posição, ícone acima do texto, ocultação da barra
desktop e acesso a Adicionar/Mais, além da restauração dos controles ao mudar
para desktop. A seleção, edição de texto, corte, pinça e persistência continuam
exercitadas no mesmo cenário.
