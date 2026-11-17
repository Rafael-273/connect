# Duração final e composição multicâmera

## Ocorrência investigada

Log do projeto `4253be4b-7326-4f58-9d07-591e10af95bf`: QC acusa divergência
absoluta de 604s depois da composição multicâmera, após 6566s de preparação.
O log não registra duração e streams de cada intermediário, nem os intervalos
ou velocidades da revisão. Portanto não permite comprovar qual arquivo
introduziu exatamente os 604s. Não houve acesso às mídias/dados de produção.

## Defeito reproduzido e corrigido

O compositor limitava o master no filtro, mas deixava a duração de saída à
mercê do framesync de `overlay`. Uma câmera retimada podia continuar ativa
além do master; `enable` controla a imagem, não encerra o fluxo FFmpeg.

Reprodução local com vídeo/áudio sintéticos: master de 4s e câmera retimada
inconsistente. Executando o código original de HEAD, a saída tinha **3030
frames / 101s**, em vez de 120 frames / 4s. Com a correção produz 120 frames,
com áudio AAC do master idêntico ao original.

- Cada câmera é recortada à duração de seu intervalo depois de aplicar a
  velocidade, com timestamps reiniciados antes de posicioná-la na timeline.
- Cada passagem FFmpeg recebe limite explícito de duração de saída.
- O pipeline passa sua duração planejada ao compositor; a revisão fornece o
  limite nos demais consumidores, como o preview.
- A saída não é preenchida para esconder falta de material. QC permanece ativo
  e rejeita intermediários curtos ou divergentes.

## Outras proteções no mesmo fluxo

- Intervalos fora da sequência, recortes negativos/invertidos e velocidades
  nulas, negativas ou não finitas falham antes da preparação das mídias.
  A duração real planejada é conferida novamente antes de finalizar o áudio.
  Há tolerância de um quadro para arredondamentos na borda da sequência.
- Checagem leve de streams/duração após montagem, áudio e multicâmera. Um master
  inválido não segue para áudio/mastering, B-roll ou overlays desnecessariamente.
- A análise profunda de áudio não roda quando os checks estruturais/duração já
  reprovaram o arquivo. Evita gastar análise de loudness/silêncios em saída inválida.
- Logs `final_master_duration_checkpoint` identificam etapa, projeto, duração
  esperada e obtida. A mensagem de erro também inclui os dois valores; a
  tolerância de qualidade não foi aumentada para aceitar a divergência.

## Validação local

- Oito testes de multicâmera passaram, incluindo FFmpeg real: cadências
  diferentes, offsets/drift, dez seleções em múltiplas passagens, áudio do
  master idêntico, duração limitada e limpeza de intermediários S3 simulados.
- Testes de rejeição de duração/velocidade, interrupção antes de áudio e antes
  de materialização, e regressão da montagem por intervalos aprovados.
- A suíte combinada final passou: **13 testes**, incluindo os testes com
  FFmpeg real e os bloqueios antecipados.
- FFmpeg estático instalado somente na venv local via imageio-ffmpeg (versão
  reportada n9.0.2). Não foram alteradas dependências/configurações de produção.
- `git diff --check` sem erros. Sem deploy, reprocessamento pago ou operações S3.

Reexecutar com FFmpeg disponível:

```sh
FFMPEG_BINARY=/caminho/ffmpeg .venv/bin/python manage.py test \
  website.test_multicam.MulticamFFmpegTests website.test_render_duration \
  website.test_external_media.FFmpegRenderSmokeTests.test_approved_final_master_does_not_apply_global_cuts_again \
  --settings=website.editor_browser_test_settings --noinput
```

A revisão de outros riscos concentrou-se nas durações, retiming, intervalos,
composição e custo das validações. Não constitui garantia de ausência de erros
em codecs, armazenamento, infraestrutura ou em todos os projetos. Antes de
reexecutar o projeto afetado em produção, conferir a revisão e validar com a
versão de FFmpeg usada pelo worker; os novos checkpoints localizam divergências
anteriores à multicâmera, caso também existam naquele material.
