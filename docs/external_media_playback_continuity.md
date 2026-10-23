# Continuidade do playback da timeline

Implementação e validação em 23/10/2026.

## Comportamento

O preview mantém dois elementos de vídeo principais. O buffer secundário é
identificado por URL e timestamp de entrada, permitindo preparar outro trecho
do mesmo arquivo. Segmentos contíguos continuam no mesmo decodificador, e a
preparação procura a próxima descontinuidade depois desses segmentos.

Uma transição que aguarda B reserva seu decodificador: a preparação de C fica
pendente até B ser consumido. Eventos antigos não podem retomar um pedido
cancelado. Pausar durante uma espera revoga também a intenção de autoplay.

A troca em reprodução exige o frame de entrada dentro da tolerância da sequência,
`readyState >= HAVE_FUTURE_DATA` e uma janela de buffer de 120 ms, ajustada pela
velocidade e limitada pelo fim do arquivo. Não se espera o arquivo inteiro.
Os dois elementos usam `preload=auto`, inclusive quando trocam de função.

Nos navegadores com callbacks nativos de vídeo, o secundário inicia brevemente
mutado e volta ao timestamp de entrada antes de ficar disponível. Isso prepara
o pipeline sem consumir o começo do take visível. Uma conclusão assíncrona
obsoleta não pode pausar o vídeo que já passou a ocupar outra função.

O limite antigo de 40 ms antes do corte foi removido. No último frame, o player
agenda a fronteira usando `expectedDisplayTime`, o tempo restante e a velocidade.
Seek, pausa, espera, erro e troca de timeline invalidam esse agendamento. Os
timers continuam sujeitos ao agendamento da thread principal do navegador.

A música permanece ativa nas trocas preparadas. Espera real, erro e seek
continuam interrompendo-a; o watchdog de 400 ms cobre ausência de avanço sem
`waiting`. A velocidade acompanha o vídeo principal e os vídeos de B-roll.
A multicâmera recebe a referência do principal quando os buffers alternam.

Respostas editoriais preservam a intenção de reprodução e evitam reposicionar
o decodificador quando fonte e frame continuam válidos. Um proxy contínuo que
fica pronto durante a reprodução é adotado por polling depois da pausa, evitando
trocar de transporte no meio de uma sequência.

O carregamento inicial também possui uma saída de segurança: depois de
`seeked`/dados disponíveis, dois ciclos de pintura podem concluir a preparação
caso um vídeo pausado não emita outro `requestVideoFrameCallback`. Isso evita
que `play()` e o callback fiquem esperando um pelo outro.

## Diagnóstico no navegador

O evento `connect:preview-transition` informa as fases `warm`, `waiting`, `cold`,
`swap` e `first-frame`, com revisão, fonte, timestamp e relógio monotônico.

No swap são informados o timestamp efetivo e o buffer à frente. `first-frame`
registra `elapsedMs`, `revealMs` e `excessCallbackGapMs`. O último mede a lacuna
entre callbacks acima da cadência nominal; não mede diretamente a apresentação
de pixels, pois o frame pausado preparado já pode estar visível antes do
primeiro callback do vídeo em movimento. Uma captura audiovisual continua
necessária para certificar lacunas visuais e audíveis.

```javascript
window.addEventListener('connect:preview-transition', event => {
    console.log(event.detail);
});
```

## Verificação

```sh
node --test static/tests/*.test.cjs
```

A suíte cobre reservas A → B → C, cortes descontínuos no mesmo arquivo,
precisão da fronteira em 0,5x/1x/2x, cancelamentos, autoplay durante espera,
preparação do pipeline, continuidade da música, velocidade, referência da
multicâmera e reconciliação editorial sem seek desnecessário.

Teste independente com mídia real, sem banco, conta ou projeto Django:

```sh
npm install --prefix /tmp/filadelfia-playback-qa playwright-core
FILADELFIA_PLAYWRIGHT_MODULE=/tmp/filadelfia-playback-qa/node_modules/playwright-core \
FILADELFIA_CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
node static/tests/editor_playback_continuity.browser.cjs
```

O teste gera WebM no Chrome e uma trilha WAV, usa os módulos reais e extrai
as funções de transporte do template. Verifica três swaps preparados, incluindo
um corte no mesmo arquivo; ausência de pausas da música nesses swaps; e
recuperação de B atrasado sem perder sua reserva para C. Sua página é uma
fixture mínima, não uma execução da interface Django completa.

## Limites de validação

A validação executada cobre Chrome desktop. Safari/iOS, Firefox, Android,
mídias H.264/AAC de produção, cortes muito curtos e captura da saída de áudio
continuam necessários antes de certificar continuidade imperceptível em todos
os ambientes. Os testes não demonstram áudio sample-perfect nem estabelecem
um p95 visual de um frame. Nenhuma alteração de backend, encoder ou formato da
timeline foi necessária nesta etapa. Os masters originais ainda precisam fazer
seek sobre cortes não incorporados; a revisão contínua já editada evita esses
saltos quando disponível e fiel à edição.
