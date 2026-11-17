# Otimizações de renderização implementadas

Implementação sobre a análise `render_performance_2026_11_16.md`. Não modifica
resolução, codec/preset/CRF configurados, cortes aprovados, LUT, correção de cor,
legendas, reenquadramento, B-roll, multicâmera, mixagem ou mastering.

## Mudanças

### Corte com busca próxima ao intervalo

A concatenação passa a fazer busca precisa de entrada com pelo menos um segundo
de preroll antes do ponto selecionado. O trim/atrim continua escolhendo os limites
exatos e a imagem/áudio são rebaseados como antes. Evita decodificar todo o começo
do mesmo intermediário em cada entrada de um grupo. Erros específicos de seek
usam o leitor anterior; timeout e falta de disco não disparam uma segunda renderização.
A concatenação permanece por filtros: não foi adotada cópia indiscriminada de
streams, que poderia reintroduzir gaps de AAC e deslocamentos de timestamps.

### Amostragem antes de filtros estáticos

Quando o enquadramento é estático, `fps=30` vem antes de escala/cor/conversão HDR,
evitando processar nesses filtros quadros que seriam descartados. Planos dinâmicos
de Auto Reframe continuam com a ordem anterior. Os testes comparam quadros RGB
decodificados e áudio entre os dois caminhos, incluindo fonte 60fps, VFR e PQ HDR.
No benchmark simples de normalização não houve ganho claro só por essa alteração;
o efeito depende do custo dos filtros e da fonte.

### Checkpoints completos de imagem

Normalizações e grupos de concatenação concluídos podem ser recuperados em
outra tentativa/exportação. O cache é separado dos proxies de reprodução.

- Fonte local identificada por SHA-256; fonte S3 por nome, ETag, tamanho e VersionId.
- Chave inclui trims, dimensões, LUT por conteúdo, intensidade, cor, enquadramento,
  preset/CRF, código e build do FFmpeg. Planos ainda não congelados não são reutilizados.
- Planos congelados não dependem do nome de um proxy temporário de análise.
- Artefato validado por estrutura/duração antes de publicar; restauração verifica
  tamanho, SHA-256 e estrutura/duração novamente. Corrupção/expiração refazem a etapa.
- A origem temporária S3 de um normalizado carrega a identidade do checkpoint,
  permitindo reutilizar seus grupos mesmo quando a URL/chave temporária muda.
- Manifesto publicado após salvar o arquivo completo. Publicações concorrentes
  podem deixar um órfão, mas o leitor só consome um manifesto completo e verificado.
- Falta de capacidade para restaurar interrompe antes de tentar recodificar a
  mesma saída sem espaço. Falhas de armazenamento do cache preservam o caminho normal.

A retomada cobre **normalização e montagem por grupos**; a finalização de áudio,
composição posterior e QC continuam executando normalmente. Não é retomada de
um FFmpeg parcialmente executado, nem reaproveitamento de arquivo reprovado.

### Limpeza de ruído sem copiar o vídeo a cada decisão

Extrai áudio uma vez, aplica o tratamento global e depois os tratamentos locais
na mesma ordem, em buffers PCM. O vídeo é copiado somente na união final com o
áudio tratado. PCM intermediário evita reencodar AAC a cada evento; RF64 automático
acomoda WAVs grandes. O provider global conserva seu processamento existente.

Só o buffer corrente e o próximo são necessários. Intermediários pertencentes ao
serviço são removidos ao avançar e no finally, inclusive em erros; a entrada
original é preservada. A checagem de capacidade estima dois buffers float PCM ou
um buffer mais o mux final, o que for maior. Falta de disco não é tratada como um
simples filtro opcional ignorado. No-op e fallback de filtros locais incompatíveis
continuam preservados. Diálogo, ducking, música e análises de mastering mantêm suas
etapas e ordem; não foram fundidos filtros cuja equivalência sonora não foi demonstrada.

## Configuração e retenção

| Variável | Padrão |
| --- | --- |
| `EXTERNAL_MEDIA_RENDER_CACHE_ENABLED` | `true` |
| `EXTERNAL_MEDIA_RENDER_CACHE_TTL_SECONDS` | `604800` (7 dias) |
| `EXTERNAL_MEDIA_RENDER_CACHE_MAX_FILE_BYTES` | `2147483648` (2 GiB por artefato) |

O cache utiliza o storage `external_media`, sob `external_media/render-cache/v1`.
Ele acrescenta armazenamento e uploads na primeira execução; um cache frio pode
ter overhead. O limite é por arquivo, não um orçamento global de armazenamento.

A tarefa `external_media.cleanup_render_checkpoints` foi adicionada ao Celery Beat
para execução diária e remove apenas arquivos expirados desse namespace. Se o
ambiente não executa Beat, configurar o agendador para o comando abaixo; nada foi
agendado nem executado em produção nesta sessão.

```sh
# Apenas lista (padrão):
python manage.py cleanup_render_checkpoints
# Remove somente checkpoints gerados e expirados:
python manage.py cleanup_render_checkpoints --apply
```

## Benchmarks locais

Fontes sintéticas; execuções locais únicas, sem S3 e sem carga equivalente ao worker:

| Caso | Antes/frio | Depois/reutilizado |
| --- | ---: | ---: |
| Oito cortes tardios em fonte de 45s, 640×360 | 2,349s | 0,864s |
| Seis tratamentos locais em fonte de 8s, 1280×720 | 4,273s | 1,057s |
| Normalização com cache, fonte 8s | 1,278s | 0,124s |
| Apenas antecipação de fps, receita simples | 1,018s | 1,086s |

Esses números não são uma previsão para os 109min do log original. O maior
benefício do cache aparece em novas tentativas/exportações com a mesma receita;
a primeira normalização de uma fonte ainda precisa ser executada.

## Testes

`website/test_render_performance.py` cobre comparação real de frames e áudio em
cortes tardios/reordenados, SDR 60fps/VFR/HDR, cache frio/quente/corrompido/expirado,
invalidação por conteúdo/receita/ETag, publicação concorrente, limpeza isolada e dry-run,
ruído global/local com sobreposição, limpeza no erro e fallback sem repetir timeouts.
Foram executadas também as regressões existentes de montagem, reenquadramento,
multicâmera, duração, áudio, ducking, diálogo e mastering. Todos os dados são locais
e descartáveis. FFmpeg/FFprobe usados: build estático 8.0, instalado na venv local;
nenhuma dependência desse pacote foi adicionada à aplicação.

```sh
PATH=/diretorio/com/ffmpeg-e-ffprobe:$PATH \
FFMPEG_BINARY=/caminho/ffmpeg FFPROBE_BINARY=/caminho/ffprobe \
.venv/bin/python manage.py test website.test_render_performance \
  website.test_external_media.AudioNoiseCleanupUnitTests \
  website.test_external_media.FFmpegRenderSmokeTests \
  website.test_multicam.MulticamFFmpegTests website.test_render_duration \
  website.test_external_media.AudioMixingSmokeTests \
  website.test_external_media.AudioMasteringSmokeTests \
  website.test_external_media.DialogueProcessingSmokeTests \
  --settings=website.editor_browser_test_settings --noinput
```

Sem deploy, acesso a mídias de usuários ou reprocessamento pago. Antes de publicar,
medir um canário com a versão de FFmpeg e os recursos do worker, incluindo I/O S3,
armazenamento do cache, cor, duração e sincronismo do material real.
