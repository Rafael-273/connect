# Transições da trilha sonora

## Análise e escopo

O editor usa JSON em `TimelineRevision`, histórico por `TimelineMutation` e uma fila
serial de operações com versão e chave de idempotência. A faixa Música já tem
snapping e edição de início/fim/source-in. O preview usa elementos de áudio no
relógio dos quadros decodificados do vídeo. A exportação monta um WAV posicionado
na timeline e depois aplica a mixagem existente, com fades, automação e ducking.

Antes desta mudança, músicas novas recebiam CONSTANT_POWER automaticamente;
o crossfade exigia sobreposição visual, o bloco era decorativo e o preview
usava toda a sobreposição mesmo quando o FFmpeg usava uma duração menor.

A implementação mantém esses componentes e a faixa única. Não exige migração
de banco: a transição integra o snapshot editorial existente.

## Contrato editorial

As músicas são ordenadas por início na timeline, inclusive após reposicionamento.
A música de entrada em `timeline.music_tracks` pode ter:

```json
{
  "track_id": 2,
  "start_ms": 5000,
  "end_ms": 10000,
  "source_in_ms": 1000,
  "transition": "NONE",
  "audio_transition": {
    "id": "music-join-2",
    "type": "CONSTANT_POWER",
    "from_track_id": 1,
    "duration_ms": 2000
  }
}
```

`audio_transition: null` remove a transição. Ela nomeia o vizinho de saída para
não se transferir silenciosamente a outra junção ao remover uma música.
Se os clipes deixarem de encostar, o elemento permanece salvo e fica suspenso.
A duração solicitada permanece no snapshot; a duração efetiva é derivada dos
arquivos originais. Outros tipos podem ser adicionados ao normalizador, ao
cálculo de geometria e ao mapeamento de curvas, sem criar outra timeline.

Músicas novas entram sem transição. Projetos antigos com strings CONSTANT_POWER
e sobreposição visual mantêm seu comportamento, inclusive controles para
alterar sua duração ou removê-la.

## Handles e paridade

`music_transitions.py` e `external_media_music_transitions.js` implementam a mesma
regra, verificada com o mesmo conjunto de fixtures JSON:

- Tenta centralizar o crossfade no corte.
- Usa o head disponível da entrada antes do corte e o tail da saída depois dele.
- Desloca a janela para um lado quando o outro não tem handles suficientes.
- Reduz a duração quando a soma disponível não atende ao pedido; sem handles,
  mantém o corte seco e informa duração disponível zero no inspector.
- Limita cada lado à metade do clipe correspondente para duas junções vizinhas
  não consumirem a mesma região do clipe.

Não move os clipes nem encurta a sequência. O WAV é completado com silêncio
até o fim da sequência para impedir que o mixer reinicie a trilha após o
último clipe. Os handles não são inventados por
looping; a repetição que já preenche clipes longos permanece compatível.
O preview usa duração do elemento de mídia; o render consulta ffprobe apenas
para os arquivos envolvidos em transições estruturadas. As curvas são cos(p·π/2) na saída e
sin(p·π/2) na entrada, equivalentes ao `afade curve=qsin` do FFmpeg.

O preview aplica os envelopes existentes depois dos ganhos da transição. O
render mistura a trilha antes da cadeia existente de fades, automação, ducking
e mastering. Os demais processamentos de áudio já existentes mantêm suas
características; o teste de paridade mede a curva e o relógio da transição.

## Fluidez e persistência

A junção oferece um botão para abrir os controles. O inspector permite escolher
Sem transição / Potência Constante e ajustar a duração em segundos. O bloco
mostra a duração efetiva e recebe destaque quando selecionado.

A interface muda localmente antes do salvamento, com debounce pela fila atual.
A fila impede que uma resposta antiga substitua uma edição ainda pendente e
mantém retry/conflito/undo/redo existentes. Mudanças de temporização não recriam
os canais de áudio. Revisões que alteram somente `music_tracks` reutilizam a
referência `review_video_revision_id` do proxy de vídeo anterior; alterações
visuais seguintes voltam a ter seu próprio proxy. Isso evita reencodar vídeo
para editar um crossfade. A referência é criada pelo servidor e resolvida
somente dentro do mesmo projeto.

## Verificação

```sh
node --test static/tests/*.test.cjs
python manage.py test website.test_music_transitions \
  website.test_external_media.AudioPlaylistTimelineTests \
  website.test_external_media.AudioMixingUnitTests \
  website.test_external_media.AudioMixingSmokeTests \
  website.test_editor_persistence
```

Com ffmpeg/ffprobe no PATH, o teste de áudio real verifica duração exata do WAV
e amplitudes dos dois tons ao longo do crossfade. Sem esses executáveis,
essa verificação específica é explicitamente ignorada.

O teste opt-in `EditorBrowserTests.test_soundtrack_transitions_and_optimistic_saves`
usa Chrome/Playwright e PostgreSQL descartável, conforme os demais testes do
editor. Verifica criação explícita, duas edições com resposta atrasada,
reabertura, remoção e desfazer/refazer.
