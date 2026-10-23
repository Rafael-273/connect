# Auditoria, correções e evolução do multicâmera

Data: 23/10/2026. Escopo: `connect/`. Implementação local, sem deploy, migração ou alteração de dados reais.

## 1. Diagnóstico inicial

A auditoria foi realizada antes de alterar o código. O repositório já continha mudanças locais em música, editor e área de trabalho; elas foram preservadas. Este relatório descreve apenas o trabalho de multicâmera e as correções de tempo/áudio diretamente relacionadas.

| Componente | Estado encontrado |
| --- | --- |
| Upload | `ProjectBlockMedia` já armazenava papel, ordem, nome, identificação e indicação de enquadramento. Views aceitavam câmeras extras nos blocos e em uploads diretos. |
| Agrupamento | `SourceManifestBuilder` agrupava uploads de um mesmo take, mas ignorava uploads sem bloco. Intervalos de um mesmo arquivo podiam ser confundidos com câmeras distintas. |
| Montagem | O manifesto marcava câmeras secundárias com `render_enabled=False`; a montagem usava somente as principais em sequência. Isso preservava a montagem existente, mas não implementava alternância. |
| Sincronização | Não havia correlação, estimativa de offset, confiança ou drift para câmeras. |
| IA/transcrição | Já existiam transcrição por palavra, cortes de fala, análise de contexto e gateway de IA. Não existia uma decisão editorial multicâmera. |
| Timeline | Já oferecia revisões imutáveis, aprovação, histórico, cortes, divisões, enquadramento, elementos e música. Não havia operação de seleção de câmera. |
| Preview | Proxies por fonte e compositor contínuo de revisão já existiam. O master do job é substituído pelo render final, o que exige uma base limpa ao revisar novamente. |
| Render | Já reconstruía fontes e intervalos aprovados; não interpretava decisões de câmera. |

## 2. Problemas identificados

1. Câmeras extras eram recebidas, mas nunca entravam na imagem final.
2. Ausência de sincronização validada e de controle manual de alinhamento.
3. Uploads diretos não participavam dos grupos multicâmera; recortes da mesma principal precisavam compartilhar a referência correta.
4. Ausência de cortes semânticos e de representação editável das escolhas de câmera.
5. Reutilizar um render final como base de nova revisão manteria alternâncias antigas mesmo depois de removê-las.
6. Encontrado em teste real: diferenças de cadência podiam produzir uma imagem da principal antes do limite esperado. A concatenação também podia sair a 25 FPS apesar da timeline a 30 FPS.
7. Falha de áudio já presente antes desta missão: `-shortest`, em conjunto com a cadência de pacotes AAC e uma codificação adicional de proxy, podia truncar caudas e acumular desvio entre takes. O teste existente com dez takes e chirp reproduzia a divergência entre preview e entrega.

Prioridade adotada: preservar áudio e relógios; validar alinhamento; usar decisões revisionadas; integrar edição manual e render; acrescentar análise semântica com limites verificáveis.

## 3. Melhorias implementadas

### Sincronização e confiança

- Extração pelo FFmpeg da primeira faixa de áudio em PCM mono a 8 kHz, limitada aos primeiros 180 segundos de cada arquivo. Arquivos de vídeo não são carregados em memória.
- Características espectrais em seis bandas, com normalização por banda; correlação FFT normalizada pela energia do trecho comum. Suporta mudanças de ganho, polaridade, filtragem de microfone e sample rate.
- Pelo menos cinco segundos de sobreposição; validação em três janelas independentes, margem contra picos concorrentes e estimativa de drift linear.
- Critérios atuais: confiança mínima 0,55; margem de pico mínima 0,08; resíduo máximo de 20 ms; drift máximo de ±1.000 ppm.
- O retorno de um offset não significa sucesso: silêncio, áudio ausente, repetição ambígua, falta de correspondência ou drift excessivo produzem `REVIEW` e mantêm a principal.
- Cache por versão do algoritmo, fontes e referência. Trocar o arquivo invalida a análise correspondente. Falhas de extração não derrubam o projeto.
- O limite validado fica registrado. Seleções além dele exigem confirmação manual do deslocamento; o algoritmo não extrapola automaticamente uma análise curta por horas de gravação.

Convenção única: `tempo_secundária = tempo_principal × rate + offset_ms`. Offset positivo avança no arquivo da câmera extra. `rate = 1 + drift_ppm / 1.000.000`.

### Decisão editorial

- Timestamps de palavras alimentam candidatos situados no meio de pausas de pelo menos 180 ms, em limites de frases ou transições de pensamento. Timestamps aproximados de segmentos não geram cortes precisos.
- Uma solicitação limitada ao gateway existente, com o mesmo `EXTERNAL_MEDIA_BROLL_MODEL`, avalia contexto e pode propor **nenhuma troca**. Não foi adicionado serviço ou modelo mais caro.
- A IA escolhe exclusivamente IDs de pausas e câmeras fornecidas. Não determina timestamps. IDs inventados e escolhas abaixo de 0,75 de confiança são descartados.
- Há no máximo 120 candidatos por solicitação e 12 escolhas semânticas; a resposta é reutilizada quando os dados relevantes não mudam.
- O plano ainda valida cobertura, sincronização, duração e espaçamento depois da resposta da IA. Uma indicação semântica não supera essas validações.
- Se o provedor falhar, há uma proposta local conservadora baseada em transições e pausas. A configuração `multicam_semantic_enabled=False` permite usar somente essa análise local.
- `multicam_min_shot_ms`, em `project.configuration`, controla a duração mínima das passagens e o espaçamento das propostas; padrão de 6.000 ms, piso de 2.000 ms. Não há alternância periódica obrigatória.

### Timeline e controle manual

- Operação `camera_selection` no conjunto de decisões existente. A principal permanece como base; as seleções substituem somente sua imagem.
- As escolhas são salvas em coordenadas absolutas da fonte e projetadas sobre os clips mantidos. Cortes de fala e divisões posteriores mantêm o alinhamento e podem fragmentar uma seleção sem perder sua identidade.
- Painel “Câmeras e sincronização” no editor e faixa “Câmeras” na mesma timeline.
- Permite adicionar, trocar a câmera extra, mover início/fim, alterar duração, remover a alternância, ajustar offset/drift e revisar a justificativa da IA.
- Edições usam os mecanismos existentes de persistência, controle de versão, histórico, aprovação e invalidação do render. Intervalos sobrepostos, fontes de outra gravação e trechos sem cobertura são rejeitados antes de salvar.
- Uploads diretos e os vários recortes de uma mesma principal compartilham o agrupamento correto. Recortar apenas a principal não cria um grupo multicâmera fictício.

### Preview, render e áudio

- Um único `MulticamRenderService` é usado pelo preview contínuo e pelo render final. Executa os segmentos congelados na revisão, sem tomar novas decisões editoriais.
- O compositor mapeia apenas o áudio do master e o copia. Nenhum áudio secundário entra na composição. Tratamento de diálogo, música e mastering já configurados continuam no pipeline existente.
- A base limpa do preview é o proxy inicial da principal. Uma nova revisão não reutiliza como base as alternâncias já gravadas na entrega anterior. Na ausência dessa base para uma entrega antiga, o worker informa a necessidade de reprocessar.
- Enquanto aguarda o preview contínuo, o navegador mostra a câmera extra em vídeo mudo sobre a principal. Essa reprodução tem fidelidade aproximada; o preview renderizado usa o compositor compartilhado e desliga a camada duplicada.
- Saída a 30 FPS e intervalos de câmera com início inclusivo/fim exclusivo. A repetição do último quadro de uma fonte a 24/25 FPS é limitada pelo próprio intervalo de exibição.
- Adicionado `apad` antes do encerramento por vídeo nos proxies/normalização; concatenação explicitamente a 30 FPS. Isso corrigiu o teste preexistente de conteúdo de áudio em dez takes.
- Versão de tempo da montagem: 3. Versão de proxy: 5. Proxies antigos são reconstruídos quando o processamento normal solicita sua preparação; revisões existentes não são reescritas silenciosamente.
- Até oito entradas de câmera por passagem do compositor, com limpeza de intermediários, armazenamento existente e execução no worker atual.

Referências técnicas usadas: [filtros e sincronização de entradas do FFmpeg](https://ffmpeg.org/ffmpeg-filters.html), [FFT real do NumPy](https://numpy.org/doc/stable/reference/generated/numpy.fft.rfft.html). As regras de confiança e edição são decisões desta implementação, não garantias desses componentes.

## 4. Arquivos modificados

| Arquivo | Alteração desta missão |
| --- | --- |
| `website/external_media/multicam.py` | Sincronização, confiança/cache, revisão semântica, decisões canônicas e compositor compartilhado. |
| `website/external_media/canonical.py` | Grupos diretos, referências de recortes da principal e alinhamento no manifesto. |
| `website/external_media/preview.py` | Projeção das câmeras na timeline, mutações revisionadas, propostas iniciais, durações de proxies e preview com base limpa. |
| `website/external_media/services.py` | Preparação no worker, preservação de palavras, render de câmeras e correção de caudas/CFR na montagem. |
| `website/views/external_media.py` e `website/urls.py` | Endpoint protegido de edição de câmeras e seleção de fonte adequada no preview. |
| `templates/member/external_media/preview.html` | Painel, faixa de câmeras e integração com o editor existente. |
| `static/external_media_multicam.js` | Controles de câmera e reprodução visual muda em fallback. |
| `website/test_multicam.py` | Testes de sincronização, semântica, persistência, mídias reais e navegador. |
| `static/tests/external_media_multicam.test.cjs` | Contrato de tempo e limites dos segmentos. |
| `static/tests/editor_multicam.browser.cjs` | Controles do componente em Chrome. |
| `static/tests/editor_multicam_integration.browser.cjs` | Fluxo no editor Django real, com persistência e recarregamento. |
| `static/tests/external_media_editor_state.test.cjs` | Atualização da dependência do harness do editor. |
| `docs/multicam_audit.md` | Diagnóstico, decisões, validações e limites. |

As diferenças já existentes em música, configurações, dashboards e área de trabalho não foram atribuídas a esta missão.

## 5. Testes realizados

Ambiente: Python 3.11.17, Django 4.2.16, NumPy 1.26.4, FFmpeg 7.0 e Chrome. PostgreSQL de teste local, armazenamento de mídia em `/tmp`, sem serviços de produção. As respostas de IA foram simuladas nos testes; não foi feita uma chamada real de IA com dados de usuários.

| Validação | Resultado |
| --- | --- |
| Suíte nova `website.test_multicam`, incluindo Chrome com servidor Django | 30 testes passaram. |
| Testes JavaScript `node --test static/tests/*.test.cjs` | 68 passaram. |
| Componente em Chrome, `editor_multicam.browser.cjs` | Passou: adicionar, mover, ajustar offset, remover e camada muda. |
| Preview e render reais da revisão aprovada | Mesmas câmeras nos quadros 15, 30, 149, 150 e 180; cadência a 30 FPS. |
| Áudio real no compositor | Pacotes AAC do master e da saída comparados byte a byte; iguais. |
| Revisão após render final | Remover alternância recupera a principal a partir da base limpa. |
| Teste existente de áudio entre proxy e entrega, com dez takes/chirp/cortes | Falhava na base anterior; passou após corrigir caudas e cadência. |
| Suíte ampliada de mídia, persistência e multicâmera | 412 executados; 6 falhas e 5 erros preexistentes restantes. Não está integralmente verde. |
| Comparação anterior isolada, antes destas mudanças | 382 executados; 7 falhas e 5 erros. Mesmas ocorrências restantes, além da falha de áudio agora corrigida. |
| `manage.py check`, sintaxe JavaScript e `git diff --check` | Sem problemas identificados. |

Cobertura dos 12 cenários obrigatórios:

1. Inícios diferentes: offsets positivos e negativos.
2. Qualidades diferentes: ganho, inversão de polaridade, filtragem e ruído.
3. Silêncio inicial: preservação da estimativa validada.
4. Durações diferentes: sobreposição parcial.
5. Baixa confiança: silêncio, repetição e gravações sem relação exigem revisão.
6. Frases: pontos entre palavras, seleção semântica, IDs inválidos e opção de manter a principal.
7. Áudio principal: igualdade de pacotes e frequência da principal durante imagem secundária.
8. Troca manual: segunda câmera extra e preservação da revisão anterior.
9. Movimentação: limites editáveis, divisões, fala removida, recortes e persistência após reload.
10. Preview/render: execução dos caminhos reais e comparação por número de quadro.
11. FPS diferentes: câmera secundária a 24 FPS e composição/entrega a 30 FPS.
12. Sem áudio: falha de extração esperada, estado `REVIEW` e ajuste manual.

Outros testes cobrem drift pequeno/excessivo, invalidação de cache, arquivos substituídos, intervalos não cobertos, sobreposições, câmera de outro grupo e valores não finitos.

### Falhas restantes da suíte ampliada

Reproduzidas também na cópia anterior às mudanças:

- Dois cenários de reutilização de nomes de templates colidem com seeds existentes; há erros adicionais no teardown após a transação quebrar (quatro erros reportados).
- Fixture de fallback B-roll não fornece `block_id` (um erro).
- Expectativas antigas de configuração de mixagem e idiomas de legendas (duas falhas).
- Expectativas de textos de histórico, exportação e tempo decorrido nas páginas atuais (três falhas).
- Página pública de revisão contém texto que um teste de restrição de faixas espera não encontrar (uma falha). Requer investigação própria; não foi tratada como validação de controle de acesso concluída.

Evidências locais da execução: `/tmp/filadelfia-multicam-baseline-clean.log`, `/tmp/filadelfia-multicam-delivery-tests.log`, `/tmp/filadelfia-multicam-semantic-tests.log` e `/tmp/filadelfia-multicam-js-final.log`. Esses logs são temporários.

Para repetir, use um settings de testes com PostgreSQL e `MEDIA_ROOT` isolados. Comandos executados na sessão incluíram:

```sh
python manage.py test website.test_multicam --settings=filadelfia_multicam_test_settings --noinput
python manage.py test website.test_external_media website.test_editor_persistence website.test_multicam --settings=filadelfia_multicam_test_settings --noinput
node --test static/tests/*.test.cjs
node static/tests/editor_multicam.browser.cjs
```

O settings usado nesta sessão está em `/tmp/filadelfia_multicam_test_settings.py`, fora do repositório. `FILADELFIA_PLAYWRIGHT_MODULE` aponta para a instalação local de `playwright-core`; FFmpeg/FFprobe também estavam no `PATH` para os testes legados que chamam os binários por nome.

## 6. Limitações restantes

- A validação acústica cobre até os primeiros 180 segundos, não uma gravação inteira de horas. Não há correção não linear de relógio. Fora da região validada é necessário ajuste manual.
- Não foi implementada uma segunda transcrição da câmera extra para validar o áudio. Isso evita novos custos de transcrição, mas deixa sinais muito degradados para revisão manual.
- Escolha semântica usa texto e indicações das câmeras; não analisa imagem, foco, oclusão ou quem aparece efetivamente em cada câmera. O modelo não recebeu vídeo.
- Sem transcrição por palavra ou pontos seguros suficientes, não há alternância automática; edição manual permanece disponível.
- O preview no navegador antes do compositor contínuo tem sincronismo aproximado. A validação por quadro se refere ao preview renderizado e ao render final.
- Uma seleção manual deve pertencer a uma gravação principal. Ela pode atravessar divisões e trechos de fala removidos dessa fonte; não atravessa gravações principais diferentes.
- Mais de oito seleções exigem passagens adicionais de codificação do master. A memória de decodificação fica limitada, mas o custo cresce em projetos com muitas alternâncias.
- Fontes secundárias usam preenchimento central do canvas. Não foram introduzidos planos próprios de Auto Reframe/LUT por câmera extra.
- Não foi executado benchmark com gravações longas reais de culto, infraestrutura S3/worker de produção ou chamada real do modelo de IA.
- As 6 falhas e 5 erros anteriores listados acima continuam exigindo tratamento independente.

## 7. Recomendações futuras

1. Medir qualidade acústica com um conjunto autorizado de gravações reais e vários microfones antes de alterar os limiares.
2. Acrescentar janelas acústicas ao longo da gravação, com alinhamento por partes e detecção de descontinuidade, mantendo a revisão manual.
3. Avaliar foco/enquadramento/presença do locutor para escolher entre várias câmeras, reutilizando os componentes visuais existentes.
4. Para projetos com muitas alternâncias, renderizar trechos em blocos e concatenar o vídeo, mantendo o master de áudio único, em vez de recodificar o master por passagem.
5. Tratar as falhas anteriores de templates, B-roll e revisão pública em tarefas próprias, com critérios de acesso e conteúdo explícitos.

## 8. Resumo executivo

O sistema agora consegue alinhar câmeras por áudio, reconhecer quando a sincronização não é confiável e propor alternâncias em pontos naturais da fala. O usuário pode revisar e ajustar todas as escolhas na timeline existente. A imagem muda sem trocar o áudio da principal; preview renderizado e entrega usam as mesmas decisões. Também foi corrigido um desvio de áudio preexistente entre proxies e originais. O código permanece local e a suíte geral ainda tem falhas anteriores documentadas.
