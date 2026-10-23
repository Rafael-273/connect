# Meu trabalho · Mídia

Acesso na Home, dentro do card Mídia Externa, para membros e líderes ativos.
Líderes também possuem o acesso na Organização da Mídia. A gestão permanece
com as permissões existentes.

## Dados e privacidade

A página utiliza `MediaTask` e `MediaContent` existentes; não há novos modelos
nem migrações. Ser responsável geral por uma Demanda não concede acesso às
etapas de outras pessoas nesta área. Até líderes só veem suas próprias etapas.

`website/services/media_work.py` concentra a consulta com `assigned_to=user`,
exclui Demandas removidas e prepara os campos permitidos para apresentação.
Os detalhes da liderança não são reutilizados. Não são enviados status geral,
responsáveis, etapas relacionadas ou comentários internos.

O detalhe apresenta título, Evento opcional, briefing, observações, prazo geral,
instruções e status da própria etapa. Textos escapados mantêm referências
clicáveis. Anexos incluem somente os gerais da Demanda e os da própria etapa;
registros inconsistentes com os dois vínculos preenchidos são excluídos.
Downloads passam por autenticação e pela mesma verificação de atribuição,
sem enviar URLs diretas do armazenamento. Isso não altera a política de
armazenamento nem os downloads das áreas existentes.

## Rotas

- `GET /media/meu-trabalho/`: pendentes por padrão; `?tab=completed` para concluídas.
- `GET /media/meu-trabalho/<pk>/`: contexto permitido da própria atividade.
- `POST /media/meu-trabalho/<pk>/status/`: somente os status existentes da etapa.
- `GET /media/meu-trabalho/<pk>/anexos/<attachment_pk>/`: download autorizado.

IDs de etapas alheias retornam 404 para leitura, alteração e download. Membros
inativos ou sem participação ativa no ministério não possuem acesso. POSTs
requerem CSRF; campos extras não alteram atribuições, textos ou datas.

A atualização bloqueia a Demanda durante a transação e chama
`sync_content_status_from_tasks`, preservando as regras e status protegidos
existentes. As outras etapas são consultadas internamente por essa função,
mas nunca devolvidas ao usuário.

## Prazos e interface

Ordenação por prazo crescente, nulos por último, desempate por ID. Grupos:
atrasadas, hoje, amanhã, próximos dias (até sete dias), mais adiante e sem prazo.
O prazo da Demanda aparece no detalhe sem substituir um prazo ausente da etapa.
Etapas da mesma Demanda permanecem separadas para preservar seus próprios prazos.
Concluídas ficam em uma aba separada, sem indicação de atraso.

`MEDIA_WORK_TIME_ZONE` define o fuso para os agrupamentos e a exibição de horários
nessas rotas; padrão `America/Sao_Paulo`. O fuso global da plataforma é preservado.

A atividade abre em um card modal sobre a lista, com o cabeçalho visual das
Demandas, fechamento por botão/Escape e rolagem interna. O detalhe é carregado
sob demanda com `?fragment=activity`, mantendo as mesmas permissões da rota
completa. Salvar o status atualiza a lista preservando pesquisa e filtros.
A rota completa continua disponível como alternativa sem JavaScript.

A listagem tem paginação de 40 etapas, campos limitados e `select_related` para
Demanda/Evento, sem N+1 ou carregamento de todos os membros e Eventos. Não há
JavaScript necessário para lista, detalhe ou atualização. A interface reutiliza
o cabeçalho, cards, botões, mensagens e navegação da plataforma.

## Verificação

```sh
python manage.py test website.test_media_work
```

22 testes aprovados em PostgreSQL isolado: isolamento entre membros e líderes,
contexto permitido, IDs alheios, anexos, exclusões, CSRF, status, recálculo,
preservação de status protegidos, ordenação, virada de dia no fuso local,
Demandas avulsas e com Evento, Home, paginação e consultas sem N+1.

Verificação no Chrome em 390 px e 1280 px: lista e detalhe sem transbordamento
horizontal; envio do formulário de conclusão e navegação para Concluídas.
`manage.py check` e `git diff --check` passaram.

A suíte ampliada de Organização/Demandas apresentou nove falhas e dois erros
preexistentes, também reproduzidos no HEAD original (`090ee08`) em checkout
separado. Envolvem edição de datas/atribuições, regras de equipe, formulário de
responsáveis, permissões esperadas por testes antigos, modal e consultas de
equipes. Nenhuma dessas rotinas foi alterada por esta implementação.
