# Editor Filadélfia — terceira etapa (P2)

Implementação incremental sobre o estado canônico, a fila e as revisões do P0,
com o DOM estável do P1. Não altera modelos, migrações, formatos persistidos ou
comportamento do renderizador.

## Interação

- **Encaixe magnético:** movimento e resize de legendas, overlays, B-roll e
  músicas procuram bordas de takes, áudio e demais elementos, início/fim da
  sequência, playhead e marcadores existentes (`markers[].time_ms`). A borda
  mais próxima dentro de oito pixels visuais vence; a tolerância em tempo
  acompanha o zoom. A seleção arrastada é excluída dos alvos. Grupos de legendas
  encaixam pelas bordas externas e preservam seus intervalos relativos.
- **Guias:** linha turquesa indica o ponto de encaixe; durante o gesto, um
  indicador mostra início/fim em timecode. Somem ao concluir ou cancelar.
  Os alvos são preparados uma vez no início do gesto e consultados por busca
  binária durante o movimento. O playhead usado é o da abertura do gesto.
- **Controle:** botão de ímã ou **S** liga/desliga o magnetismo durante a sessão;
  **Alt** o suspende temporariamente durante o arraste. A quantização por quadro
  continua ativa com o magnetismo desligado.
- **Precisão:** o transporte exibe `HH:MM:SS:QQ`. Botões e setas avançam por
  índices de quadros, convertendo para milissegundos inteiros em cada avanço,
  sem somar repetidamente um intervalo arredondado. O formato é sem drop-frame;
  as sequências atuais são compostas a 30 quadros/s.
- **Painel de tempos:** selecione uma legenda, elemento visual ou música e
  pressione **T**, ou use o cronômetro. Defina o quadro inicial (base zero) e o
  final exclusivo. A aplicação valida duração, limites da sequência e
  sobreposição de legendas da mesma faixa. Música mantém o mínimo existente de
  um segundo. O intervalo é atualizado localmente e salvo pela fila existente.
  Um deslocamento integral de música preserva sua posição de origem; um ajuste
  do início que altera a duração também ajusta o recorte da origem.
- **Seleção múltipla:** as legendas selecionadas acompanham visualmente o drag.
  Ajuste numérico exige uma única seleção. Movimento não altera a duração de
  intervalos antigos que estejam fora da grade; apenas tempos editados são
  quantizados. Não há normalização retroativa de projetos.
- **Cancelamento:** `pointercancel` descarta o movimento transitório, remove as
  guias e não envia uma alteração editorial.
- **Acesso:** os nomes das faixas permanecem visíveis no scroll horizontal e a
  barra de ferramentas pode rolar verticalmente em janelas baixas.

## Atalhos

| Tecla | Ação |
| --- | --- |
| Espaço | Reproduzir / pausar |
| ← / → | Recuar / avançar um quadro |
| Shift + ← / → | Recuar / avançar um segundo |
| Ctrl/Cmd + ← / → | Recuar / avançar cinco segundos |
| ↑ / ↓ | Take anterior / próximo |
| Home / End | Início / fim da sequência |
| V / B | Seleção / lâmina |
| C ou Ctrl/Cmd K | Cortar no playhead |
| S / Alt durante drag | Alternar / suspender magnetismo |
| T | Ajustar tempos por quadro |
| + / − | Aumentar / diminuir zoom |
| Ctrl/Cmd Z | Desfazer |
| Shift Ctrl/Cmd Z ou Ctrl/Cmd Y | Refazer |
| Del | Remover seleção, conforme os tipos já suportados |
| ? | Abrir ajuda de atalhos |

Campos editáveis e modais bloqueiam atalhos editoriais. A janela de ajuda pode
ser fechada com Escape. O ícone de reprodução acompanha os eventos reais do
vídeo, inclusive pausas causadas pelo scrubbing.

## Persistência e limites

O novo painel usa as mesmas operações parciais de tempo e playlist do editor;
nenhuma gravação ocorre durante `pointermove`. Undo/redo continua passando pela
barreira de salvamento e pelo histórico do servidor. O teste verifica ajuste,
desfazer, refazer e recarga. Movimentos de várias legendas continuam usando uma
operação/histórico por legenda, como antes; não foi criado um endpoint de lote.
Os marcadores usados são os já gerados por bloco; não foi adicionado um editor
de marcadores. Arraste de takes da montagem principal mantém suas regras atuais.

## Validação

- **39 testes JavaScript:** incluem encaixe pelas duas bordas, resize,
  desativação, tolerância em diferentes zooms, limites, preservação de duração
  antiga e dez mil avanços sem deriva em 24, 25, 30, 60 e 29,97 quadros/s.
- **18 testes Django:** onze do protocolo P0 e sete regressões editoriais.
- **3 testes Chrome:** interação real de ponteiro, uma gravação por gesto de
  resize/movimento individual, recarga, tempos por quadro em zoom 16×,
  validação de sobreposição, undo/redo, Alt, cancelamento sem gravação, toggle,
  transporte, bloqueio de atalhos em modal e acesso ao último botão da barra.
  Também repetem as verificações do P1 com vídeo/áudio reais e mil legendas.
- Na execução de regressão, os mil elementos mantiveram seus nós e o p95 do
  evento até o próximo frame foi aproximadamente **2,2 ms**. É uma medição do
  cenário automatizado local, não um compromisso de desempenho em todo projeto.
- Banco PostgreSQL e mídia de teste isolados. Conferência visual do painel no
  Chrome e `git diff --check` sem problemas.

```sh
node --test static/tests/*.test.cjs
# O ambiente Django deve apontar para banco e armazenamento descartáveis.
FILADELFIA_PLAYWRIGHT_MODULE=/caminho/node_modules/playwright-core \
FILADELFIA_CHROME_PATH='/caminho/Google Chrome' \
python manage.py test website.test_editor_browser website.test_editor_persistence \
  --settings=configuracao_de_testes --noinput
```

## Publicação

Não houve deploy. Publicar o template e os arquivos estáticos, incluindo
`external_media_editor_precision.js`, e executar `collectstatic` no ambiente de
publicação. Esta etapa não adiciona migrações. A migração `0118` do P0 continua
necessária caso as etapas anteriores ainda não tenham sido ativadas.
