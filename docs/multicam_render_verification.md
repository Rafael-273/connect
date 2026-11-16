# Verificação da renderização com câmeras extras

Data: 16/11/2026. Verificação local do código e de renders sintéticos, sem usar projeto, banco ou arquivos de produção.

## Resultado

**Não foi encontrado erro de renderização nos cenários executados.** As escolhas automáticas e manuais chegam ao mesmo `MulticamRenderService`, que aplica os segmentos congelados na revisão aprovada. Os três testes de integração de entrega executaram `ExternalMediaProjectPipeline.render()` até `FINISHED`, com a revisão renderizada correta e relatório de qualidade aprovado.

Não foi necessário alterar o compositor ou o pipeline do produto nesta verificação. Foram ampliados os testes de regressão e atualizado o teste de navegador para os controles atuais da revisão. As mudanças de estabilidade do preview da rodada anterior foram preservadas.

## Cenários executados

| Cenário | Evidência e resultado |
|---|---|
| Câmera adicionada manualmente | Persistência, preview contínuo, aprovação e render completo passaram. Principal antes de 1 s, extra de 1 a 5 s, retorno à principal no frame 150, em 30 fps. |
| Câmera adicionada automaticamente | A revisão inicial realmente executa a proposta automática por pausas de palavras. Seleção de 1,1 a 9,9 s preservada no preview e na entrega; frames 32/33 e 296/297 verificam as fronteiras. |
| Sugestão da IA alterada manualmente | Resposta editorial determinística gera seleção com origem `AI`; editar pela revisão muda para `USER`, com offset manual de 100 ms. Após excluir fala entre 3 e 5 s, os segmentos se tornam 2–3 s e 3–6 s, buscando a câmera em 2,1 s e 5,1 s. Preview e entrega de 8 s/240 frames passaram. Não houve chamada externa de IA. |
| Duas câmeras extras | Principal vermelha, extra azul a 24 fps e extra verde a 25 fps. Alternância azul → verde → principal → azul, cobrindo todos os 120 frames de saída a 30 fps. Inclui offsets positivo/negativo e rate 1,0005. |
| Mais de oito seleções | Dez intervalos forçam duas passagens do compositor. Todos os 126 frames foram conferidos; não sobraram arquivos intermediários `multicam-pass-*`. |
| Áudio | Pacotes AAC do master e da saída do compositor iguais byte a byte, inclusive com duas passagens e extras sem áudio. No pipeline completo, a frequência da fala principal continuou em 440 Hz durante imagens das extras de 880 Hz. |
| Revisar novamente após entregar | Remover a câmera após um render recupera a imagem da principal no preview contínuo, usando a base limpa em vez de reaproveitar a imagem já composta. |
| Edição no navegador | Chrome + servidor Django de testes: adicionar, salvar, recarregar, editar início/offset, remover e verificar a revisão persistida. Passou. |
| Retry e aprovação | Reenvio com o mesmo ID de operação não duplica a edição; reenvio da aprovação não agenda novamente. Aprovação fixa os ponteiros na mesma revisão manual. O envio para a fila foi simulado, não executado em worker real. |
| Validações existentes | Sincronização acústica, confiança insuficiente, ausência de áudio, cobertura, sobreposição, grupo de gravação, valores inválidos, cortes e mudança de câmera permaneceram aprovados. |

O padrão de saída verificado é 30 fps; os intervalos são inclusivos no início e exclusivos no fim. As extras substituem a imagem, enquanto o áudio principal permanece como base. A composição de B-roll/overlays ocorre depois das câmeras no pipeline; legendas finais seguem a etapa de saída existente. A combinação completa com todas essas camadas não recebeu novos testes nesta execução.

## Testes e ambiente

| Execução | Resultado |
|---|---|
| `website.test_multicam`, após ampliar os testes e executar o pipeline completo | 37 testes: 36 aprovados e 1 teste de navegador ignorado no container sem Chrome. |
| Novo teste de duas extras, adicionado após o início da execução acima | 1 aprovado com FFmpeg real. |
| Teste de navegador ignorado no container, executado separadamente no host | 1 aprovado com Chrome e Django. |
| Verificação estática | Sintaxe Python/JavaScript e `git diff --check` aprovados. |

São **38 métodos de teste distintos aprovados** nas execuções combinadas. Os testes reais usam vídeos sintéticos de 160×120, H.264/AAC, FFmpeg/FFprobe 5.1.9 e arquivos temporários. O ambiente de mídia foi um container descartável, com repositório montado somente para leitura, SQLite isolado, storage local em `/tmp`, broker de memória e rede desconectada durante os testes. FFmpeg/FFprobe também foram encontrados no worker local já em execução, por consulta de leitura.

O Chrome 141 foi executado no host com Python 3.12/Django 4.2.16, banco de testes e storage isolados. O container de mídia usou Python 3.10/Django 4.2.16. Não foi feita uma validação de locks concorrentes em PostgreSQL nesta rodada; o teste de retry verifica a semântica da operação serializada.

O teste de navegador antigo esperava o painel de câmeras aberto e botões que já não existem. Ele foi ajustado para adicionar pelo botão da timeline e editar/remover pelo inspetor, usando o formato atual `mm:ss.mmm`. A falha encontrada era do harness desatualizado, não da renderização.

Arquivos de testes:

- `website/test_multicam.py`: novos cenários de render automático, edição da sugestão, cortes, duas extras, múltiplas passagens e aprovação idempotente; o teste existente de entrega passou a executar o pipeline completo.
- `static/tests/editor_multicam_integration.browser.cjs`: fluxo atual de revisão e configuração opcional do caminho do Chrome.

Para repetir em ambiente de testes isolado com FFmpeg disponível:

```sh
python manage.py test website.test_multicam --settings=SEU_SETTINGS_ISOLADO --noinput

# Inclui o navegador, se disponível no mesmo ambiente do runner:
FILADELFIA_PLAYWRIGHT_MODULE=/caminho/para/playwright-core \
FILADELFIA_CHROME_PATH=/caminho/para/google-chrome \
python manage.py test website.test_multicam.CameraEditorBrowserTests \
  --settings=SEU_SETTINGS_ISOLADO --noinput
```

Os logs desta execução ficam em `/tmp/connect-multicam-check/`: `multicam-final.log`, `two-cameras.log`, `api-retry-approval.log` e `browser-current.log`. São evidências temporárias locais, sem dados de usuários.

## Limites do parecer

Nenhum projeto específico de produção foi renderizado ou modificado. O resultado confirma os caminhos exercitados com fixtures; não garante ausência de erro em arquivos corrompidos, codecs não testados, gravações longas, armazenamento S3 ou infraestrutura remota. O áudio sintético e os campos de alinhamento não substituem uma avaliação de sincronismo labial com gravações reais.

Não foram executados deploy, alteração de schema ou publicação de mídia. Nenhum serviço local existente foi reiniciado.
