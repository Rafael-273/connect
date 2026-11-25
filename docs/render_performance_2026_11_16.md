# Análise do tempo de renderização — log de 16/11/2026

Análise do arquivo `connect-video-editor-process_video_work-trn-09l4gdbtp74619in0008mt0pg-2026-11-16-2036.log`, confrontada com o pipeline atual. Este relatório avalia oportunidades; não modifica o motor, a qualidade ou configurações de produção.

## Medições do log

| Etapa | Tempo observado | Parcela dos 6566,26s |
| --- | ---: | ---: |
| Normalização principal | 65min18s | 59,7% |
| Grupos de cortes/concatenação + concatenação final | 31min02s | 28,4% |
| Finalização de áudio | 6min55s | 6,3% |
| Composição multicâmera | 4min47s | 4,4% |
| Outros passos, verificações e intervalos | ~1min24s | ~1,3% |
| Total da preparação, que terminou em falha | 109min26s | 100% |

A montagem completa ocupa 5793,73s (96min34s; 88,2%). Sua entrada principal tem 9,51GB decimais; o intermediário produzido tem ~949MB. A normalização codifica envelopes de 1394,771s para 1327,562s selecionados: apenas ~4,8% da duração desses envelopes é descartada. Portanto a simples antecipação dos cortes não explica uma economia enorme na primeira normalização deste caso.

Os cinco grupos de concatenação levam 176,382 + 359,543 + 324,777 + 385,969 + 371,522s; a união final leva mais 243,710s. O código abre os mesmos normalizados como entradas separadas para cada intervalo, faz trim/atrim em filtros, aplica enquadramento e recodifica. A árvore de concatenação então recodifica a união novamente.

A correção anterior de duração afeta o trecho multicâmera/checagens e evita uma classe de falhas. Ela não elimina o gargalo de 96 minutos da montagem.

## Problema adicional comprovado: disco e redução de ruído

O workspace começa com aproximadamente 2GiB livres. O log registra seis `noise_cleanup_local_skipped`, com `No space left on device`. As mensagens do FFmpeg aparecem repetidas em diferentes exceções; contar essa string não equivale a contar operações.

`AudioCleanupService.apply` cria `noise_local_0.mp4`, `noise_local_1.mp4` etc. Cada passe copia o vídeo completo e recodifica o áudio inteiro para tratar um trecho de centenas de milissegundos. Os resultados anteriores ficam no diretório enquanto `current` avança. Quatro intermediários de ~446MB já consomem perto de 1,8GB; os seguintes falham, gastam processamento e são ignorados pelo fallback existente.

Isso já causa perda de tratamentos solicitados no fluxo observado. Não deve ser tratado como uma otimização aceitável.

## Oportunidades, por prioridade

### 1. Corrigir o ciclo de vida dos intermediários de ruído

Após um passe bem-sucedido, liberar apenas o intermediário criado pelo próprio serviço que não seja mais necessário. Preservar a entrada original, o arquivo corrente e arquivos necessários a retries. Validar capacidade antes do próximo passe e distinguir falta de disco de filtro incompatível; não repetir várias operações condenadas à mesma falha.

Benefício: limita o crescimento do disco e permite executar os tratamentos que hoje falham. A economia de minutos é secundária; a principal melhoria é correção e confiabilidade. Não resolve o gargalo de 65min.

### 2. Evitar decodificações e recodificações redundantes na montagem

Implementar um caminho validado para agrupar intervalos da mesma fonte, fazer buscas próximas ao trecho e preservar os timestamps exatos. Os grupos atuais podem abrir até oito entradas do mesmo MP4 remoto, cada uma com um decoder e um filtro de trim. É preciso medir bytes realmente transferidos e tempo por grupo.

Para intermediários já cortados, enquadrados e uniformizados, avaliar concatenação com cópia de streams, com verificação de codec/extradata, timebase, resolução, áudio, PTS/DTS e priming AAC, e fallback para o método atual. Não substituir o filtro de concat por demuxer indiscriminadamente: o código atual resolve problemas reais de timestamps, sincronização e memória.

Maior alvo depois da normalização: os 31min02s desta execução. Nenhum ganho específico foi medido ainda.

### 3. Cache da normalização de qualidade final

A chave precisa incluir identidade/versão do arquivo, trims/envelope, canvas, orientação, receita HDR/SDR, LUT e intensidade, transformações, cor, versão dos filtros, FFmpeg e configurações de encoder. Só reutilizar quando a receita coincidir. O cache deve ser separado do proxy de preview e ter política de armazenamento/expiração.

Pode evitar refazer o passo de 65min ao exportar novamente o mesmo material com alterações posteriores (por exemplo música ou legenda). A primeira exportação continua precisando desse trabalho. Mudanças de enquadramento/cor que afetem a receita invalidam o cache correspondente.

### 4. Perfil da normalização principal

Medir CPU, memória, resolução/fps/codec de entrada, formato HDR, filtros ativos e velocidade do FFmpeg por receita. O log mede somente o processo inteiro. Não permite separar decodificação, transferência, filtros e encode.

No código, o filtro `fps=30` vem depois de operações como HDR/reframe/scale. Se a fonte tiver fps maior, avaliar a redução de frames antes de filtros caros, comprovando equivalência dos quadros selecionados, enquadramento temporal, cor e sincronismo. Evitar prometer ganhos sem conhecer a fonte.

Não usar proxies na entrega e não reduzir resolução, fps final, qualidade ou recursos. O preset intermediário padrão já é `superfast`; trocar cegamente o preset tem retorno incerto e altera compressão/arquivos. Concorrência deve respeitar CPU/RAM/disco: aumentar workers sem esse limite pode piorar custo e estabilidade.

### 5. Processar áudio sem copiar o vídeo a cada correção

Extrair o áudio uma vez, manter a mesma ordem dos tratamentos de ruído, diálogo, ducking, música e mastering; fazer um único mux final com o vídeo. Preservar análises de duas passagens exigidas por loudnorm e o tratamento de intervalos sobrepostos. A equivalência não se resume a juntar strings de filtros: ordem, continuidade do estado, resampling e limites dos trechos alteram o som.

Ataca parte dos 6min55s e o problema de disco, mantendo as funcionalidades.

### 6. Reutilizar artefatos válidos entre tentativas

Persistir checkpoints por receita/revisão e só retomar após verificar integridade. Uma falha tardia não deveria obrigar uma nova execução de 96min de montagem se a entrada, a revisão e os resultados anteriores não mudaram. Nunca reutilizar um artefato que falhou no QC. Isso requer controle de expiração e limpeza de temporários próprio, e não apenas deixar arquivos no workspace.

## Cuidados com a interpretação das métricas

`FFmpegRunner._log_io_metrics` define `remote_read_duration_seconds` como todo o tempo do subprocesso quando há entradas remotas. Não é uma medição exclusiva de rede. `bytes_read_from_s3_estimate` soma o tamanho dos objetos/entradas e pode contar o mesmo objeto várias vezes; não mede os bytes de Range realmente transferidos. Portanto o log não prova que S3 seja o principal gargalo, nem quantifica tráfego/fatura de rede.

Os 8 inputs de um grupo também não significam oito downloads completos: precisamos medir as requisições/bytes reais para separar custo de rede e decodificação.

## Expectativa de ganho e validação necessária

Há potencial expressivo sem retirar funcionalidades, sobretudo evitando trabalho repetido. Como ilustração matemática, não previsão: reduzir pela metade os 3918s da normalização economizaria ~32min39s, levando esta preparação de ~109 para ~77min, se o restante fosse igual. Reduzir apenas o áudio não resolve o tempo total.

Antes de publicar cada otimização: benchmark em fonte pequena e representativa com a mesma receita; comparar quantidade de frames, duração, ordem/cortes, transições, reenquadramento, cor/HDR, áudio e legendas; depois um canário controlado. Medir CPU/RAM/disco e tempo por etapa, e manter fallback quando o caminho rápido não satisfizer o contrato. Nenhuma renderização paga ou alteração de produção foi executada nesta análise.
