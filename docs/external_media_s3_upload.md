# Upload de vídeos grandes ao S3

## Causa e correção

O fluxo anterior enviava todo arquivo em um único `PutObject` assinado.
Um vídeo de 8 GB excede o limite de 5 GB dessa operação; os avisos de Tailwind
CDN e da meta tag de instalação não causam essa falha.

Referência: [limite de um PUT no S3](https://aws.amazon.com/s3/faqs/).

Arquivos menores que 100 MiB continuam usando um PUT. A partir desse tamanho,
o servidor inicia um multipart upload. O navegador usa `Blob.slice`, partes de
64 MiB e até três envios simultâneos, sem ler o arquivo inteiro em JavaScript
nem encaminhar seus bytes pelo Django. Um arquivo de 8 GiB usa 128 partes.
O tamanho da parte cresce automaticamente se necessário para respeitar 10.000
partes por upload. O limite configurado em `EXTERNAL_MEDIA_MAX_UPLOAD_MB`
continua sendo aplicado; o padrão atual é 10240 MiB.

O mesmo transporte atende os formulários de takes de blocos, blocos
personalizados, câmeras extras e B-roll que já usam presign. O fallback local
sem S3 continua com seu POST de arquivo.

## Protocolo

1. O presign existente retorna `upload_mode`, tamanho de parte, endpoint de
   assinatura e uma alocação assinada com chave, tamanho, upload ID e token.
2. Cada worker solicita a URL de sua próxima parte e envia somente aquela fatia.
   URLs são emitidas sob demanda, por uma hora, em uma sessão válida por 24 horas.
3. Falhas transitórias recebem até três tentativas com nova assinatura. Partes
   concluídas não são retransmitidas. Uma falha definitiva interrompe workers,
   aborta requisições e solicita o cancelamento da sessão no S3.
4. Somente depois de todos os PUTs terminarem, o navegador chama o complete
   existente. O servidor lista as partes, valida sequência e tamanho de cada uma,
   coleta os ETags e solicita a montagem. Esse ponto exige ausência de PUTs em
   andamento; a alocação de chave única isola a sessão de outros uploads.
5. O servidor confere tamanho e token no objeto montado antes de registrar a
   mídia e iniciar seu preview. Uma resposta perdida pode ser repetida sem
   retransmitir o arquivo: objeto concluído e registro existente são reconhecidos.

O navegador não precisa ler ETags via CORS, pois o backend verifica as partes
no S3. A assinatura vincula os dados da sessão ao projeto; o endpoint de partes
rejeita alterações de tamanho, chave ou upload ID e projetos fora de rascunho.
O cancelamento continua disponível depois de uma mudança de status do projeto.

As chaves multipart recebem um sufixo aleatório: envios que alocam a mesma
posição antes de concluir não sobrescrevem os bytes uns dos outros. Conflitos
de posição no registro continuam sendo tratados pela validação existente.

## Publicação e permissões

Publicar backend, template e `static/external_media_s3_upload.js` juntos, incluindo
o `collectstatic` usado pelo ambiente. Reabrir a página do projeto antes de
selecionar o arquivo para não manter o JavaScript antigo.

O principal IAM do backend precisa das ações de objeto `s3:PutObject`,
`s3:GetObject`, `s3:ListMultipartUploadParts` e `s3:AbortMultipartUpload` no prefixo
privado dos uploads. Preservar as permissões KMS necessárias se o bucket usar
criptografia com chave KMS. Não foram alteradas permissões nem configurações do
bucket nesta implementação.

Referência: [permissões das operações S3](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-with-s3-policy-actions.html).

Os PUTs continuam sujeitos ao CORS existente do bucket para a origem da aplicação.
A limpeza por erro é automática enquanto a página está aberta. Para abas fechadas
ou navegadores encerrados, configurar uma regra `AbortIncompleteMultipartUpload`
no prefixo dos uploads, com prazo maior que a sessão de 24 horas. A regra deve
tratar apenas uploads incompletos, preservando objetos concluídos.

Referência: [limpeza de multipart uploads incompletos](https://docs.aws.amazon.com/AmazonS3/latest/userguide/mpu-abort-incomplete-mpu-lifecycle-config.html).

Não há retomada persistente após recarregar/fechar a página; as retentativas são
por parte durante a sessão aberta.

## Testes

```sh
python3 -m unittest website.test_s3_upload
node --test static/tests/*.test.cjs
```

Os testes de protocolo simulam um arquivo de 8 GiB sem alocar seus bytes e
validam divisão, concorrência, retentativas, cancelamento, expiração, verificação
de tamanho, paginação e repetição segura da conclusão.

Smoke test opcional com boto3 e S3 emulado:

```sh
python3 -m venv /tmp/filadelfia-upload-qa
/tmp/filadelfia-upload-qa/bin/pip install boto3 'moto[s3]'
/tmp/filadelfia-upload-qa/bin/python -m unittest website.test_s3_upload_integration
```

Esse teste transfere e monta 11 MiB em três partes reais no emulador, conferindo
tamanho, metadados e uma segunda conclusão. Nenhum teste envia 8 GB ao bucket
real. IAM, CORS, rede e limite efetivo de produção precisam ser verificados no
ambiente publicado.
