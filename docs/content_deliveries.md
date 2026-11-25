# Entrega de arquivos nas etapas

A implementação mantém Django Forms, os templates do hub e os gates de Mídia
Externa. `MediaContent` é a demanda; `MediaTask` é a etapa. Evento continua opcional.
A configuração é feita no multiselect de cada etapa, ao criar/editar a demanda.
Etapas sem requisitos não mudam de comportamento.

## Estrutura

- `ContentFile`: registro reutilizável do objeto físico, com nome original, MIME,
  tamanho, SHA-256, origem, criador e metadata. A unicidade do hash e do caminho
  fica no banco. Uma reserva ainda não confirmada tem `ready=False`.
- `MediaDeliveryRequirement`: formato estruturado, nome livre para Outro,
  obrigatoriedade, orientações e etapa. Desativar um requisito preserva versões.
- `MediaDelivery`: vínculo com o arquivo e requisito, autor, versão e identificador
  idempotente. A versão mais alta é a atual; versões anteriores continuam acessíveis.
- Remover a entrega marca a versão atual como retirada, sem apagar o objeto ou o
  histórico. Uma versão anterior não volta a ser atual automaticamente. Se a etapa
  já estava em revisão ou concluída, o novo envio exige confirmação do responsável.
- `MediaAsset.content_file`: ligação do render existente com o registro central.
  O pacote Premiere de `ExternalMediaProjectExport` não é um vídeo final e não
  aparece como entrega. Os vídeos finais são os `MediaAsset` do render atual ou
  do histórico de processamentos concluídos do projeto.

As migrations `0122_content_deliveries`, `0123_editor_content_file`, `0124_delivery_review`, `0125_per_delivery_review` e `0126_confirm_corrected_deliveries` são aditivas.
Não preenchem requisitos nem alteram demandas antigas. Aplicar com:

```sh
python manage.py migrate
```

## Upload e integridade

O navegador calcula SHA-256 incrementalmente em um Web Worker, em blocos de 4 MB.
O backend reserva o hash em uma transação. O índice único arbitra solicitações
simultâneas. Um envio concorrente de outro usuário aguarda a confirmação ou a
expiração da reserva; uma reserva expirada recebe outro token. Arquivo confirmado
é reutilizado sem URL de upload e sem envio de bytes.

No S3, o navegador envia diretamente com PUT assinado incluindo tamanho, tipo e
checksum SHA-256. O S3 valida os bytes; a confirmação verifica tamanho e checksum
com HEAD e lê apenas os primeiros 4096 bytes para validar assinatura do tipo.
Não há vídeo atravessando o Django. O PUT único foi escolhido para verificar o
SHA-256 do arquivo completo: o checksum SHA-256 de multipart é composto e não
equivale ao hash binário completo. O limite padrão é 2048 MB e o teto do fluxo é
5 GiB. Falhas permitem repetir o envio; não há retomada de partes nesta fase.

Referência da validação S3:
[PutObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html).

Tipos iniciais: PNG, JPEG, WebP, GIF, PDF, PSD; vídeo: MP4, MOV e WebM. Extensão,
MIME declarado e assinatura devem corresponder. Resolução é orientação, com
aviso para render horizontal quando o requisito é Story/Reels.

O backend local mantém uploads manuais fora de `MEDIA_ROOT`, em
`.private_content_files` (configurável por `CONTENT_FILES_ROOT`), e fornece o arquivo
pela view autenticada. Ele valida SHA-256 lendo blocos do arquivo temporário.
Esse fallback de desenvolvimento não é o fluxo de vídeos grandes em produção.

## Editor, permissões e retenção

Novos renders são indexados pelo worker que já gera o vídeo. Um render antigo
sem SHA-256 é indexado pelo Celery quando selecionado: a interface aguarda o
processamento e então cria o vínculo, sem baixar/enviar o vídeo pelo navegador.
É necessário manter o worker Celery existente em execução. Erros de indexação
permitem tentar selecionar a exportação novamente.

A consulta de projetos usa a mesma permissão atual do Editor: membros ativos da
Mídia Externa podem acessar os projetos do módulo. Os projetos próprios aparecem
primeiro; o popup permite busca por nome do projeto ou responsável, com resultados paginados. Não foi inventado um compartilhamento por projeto.

Somente o responsável pela etapa pode enviar versões. Líderes ativos e os
administradores permitidos pelo módulo podem consultar arquivos e histórico.
Cada download revalida a permissão da etapa; a URL S3 expira em 5 minutos e a
resposta autenticada não pode ser armazenada em cache.

As rotinas de substituição e remoção de renders preservam objetos finais
confirmados no registro central, inclusive os vinculados a entregas. Não há
expiração automática desses objetos nesta fase. O registro central não depende da permanência da linha `MediaAsset`
para abrir uma versão histórica. Não há remoção automática de entregas nesta fase.
Não foi encontrada configuração de lifecycle do bucket no repositório; regras
externas do bucket devem preservar `contents/` e os outputs oficiais referenciados,
assim como devem preservar os demais arquivos permanentes da aplicação.

Para S3, o CORS do bucket precisa permitir PUT e os headers `Content-Type` e
`x-amz-checksum-sha256` na origem do portal. A credencial da aplicação precisa
assinar PUT/GET, consultar HEAD com checksum e ler o cabeçalho do objeto. Arquivos
usam o storage privado existente (`external_media`, prefixo `private_media`).

## Conclusão e concorrência

Configuração, criação de versão e conclusão serializam as alterações usando a
mesma demanda/etapa; os índices únicos protegem hash, número de versão e ID de
envio. A validação também está em `MediaTask.clean/save`, cobrindo formulário,
conclusão pessoal e ação da liderança. Requisitos opcionais/inativos não bloqueiam.
Quando todos os arquivos obrigatórios chegam, a etapa entra em revisão. A liderança avalia cada entrega no card de revisão. Uma reprovação permite informar
o ajuste necessário e devolve a etapa ao responsável; as aprovações dos outros
formatos são preservadas. Após enviar uma correção, o responsável confirma em
“Concluir e enviar para revisão”; até essa confirmação a versão é um rascunho e não
pode ser avaliada. A etapa só é concluída quando todos os arquivos enviados
estão aprovados e os requisitos obrigatórios foram preenchidos. Cada decisão registra
a versão avaliada e uma revisão antiga não pode aprovar versões novas. A etapa bloqueia
novas versões enquanto aguarda revisão. Etapas sem requisitos
obrigatórios conservam o fluxo de conclusão anterior. Mudanças nos requisitos durante
a revisão devolvem a etapa para execução.

O futuro catálogo pode consultar `ContentFile → MediaDelivery → requisito → etapa
→ demanda → evento` sem copiar objetos. `format=telao` é estruturado para consulta
futura; biblioteca, publicação, revisão avançada e integrações não foram criadas.

## Verificação

```sh
python manage.py test website.test_content_deliveries website.test_media_work website.test_s3_upload
node --test static/tests/delivery_hash.test.cjs
python manage.py check
python manage.py makemigrations --check --dry-run
```

Os testes S3 usam mocks; não fazem upload ao bucket real. Os testes de unicidade
são portáveis; a serialização por `select_for_update` precisa de PostgreSQL em
produção. SQLite não valida bloqueios de linha entre transações concorrentes.

Validação desta implementação: 74 testes de backend (36 da feature, 26 de Meu
trabalho e 12 de upload S3) e 12 testes Node de SHA-256 passaram. Também passaram
`check`, conferência de migrations, compilação Python, Pyflakes dos novos módulos,
sintaxe JavaScript, `git diff --check` e `collectstatic` em diretório temporário.
Os testes foram executados em SQLite temporário porque não havia PostgreSQL local.
Não houve conexão com o S3 real nem verificação visual em navegador.

As suítes ampliadas de Organização (62 testes) e Editor (93 testes) apresentaram,
respectivamente, seis falhas/um erro e cinco falhas/um erro. Os mesmos testes e
resultados foram reproduzidos no commit anterior `10c0a83`, em worktree isolada,
com a mesma configuração de teste. As falhas preexistentes não foram alteradas
para acomodar a feature.
