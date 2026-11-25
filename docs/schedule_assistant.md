# Assistente de Escalas — infraestrutura e planejamento mensal

## Acesso e escopo

**Dashboard → Mídia Externa → Secretaria → Escalas → Criar escala automática.**

O mês e o ano iniciam ou retomam o mesmo rascunho ativo da Mídia. A área de Escalas lista os mesmos `MonthlySchedule` da Administração Geral; não há outro cadastro de escalas. O detalhe administrativo de um rascunho oferece um link para o planejamento na Secretaria.

As etapas 1 (Planejamento), 2 (Disponibilidades) e 3 (Geração e revisão) estão funcionais. A etapa 2 é descrita em `docs/schedule_availability.md`, incluindo modelos, APIs e regras de revisão. A etapa 3 produz sugestões determinísticas no rascunho, sem publicar a escala.

A autorização segue o sistema atual: liderança ativa da Mídia e usuários administrativos (`is_staff` ou `user_type=admin`). Administradores não precisam ter um `Member`. IDs de outros ministérios e de outros rascunhos são recusados. Não foram criados grupos ou permissões novos.

## Calendário e agenda

- O calendário apresenta o mês inteiro, com grade de 42 dias, visão em lista e editor de ocorrência. Domingo de manhã e domingo à noite são ocorrências distintas.
- Cultos recorrentes e suas datas adicionais explícitas entram automaticamente no novo rascunho. Eventos extraordinários são adicionados pelo seletor da agenda.
- A data de referência de `Event` não é tratada como início de vigência: o cadastro atual a calcula a partir do próximo dia da semana. A expansão semanal e quinzenal cobre o mês completo, respeitando o dia/intervalo de referência, inclusive antes dessa data.
- Recorrência mensal utiliza o dia do mês da data de referência. Meses sem esse dia não recebem uma ocorrência inventada. Recorrências personalizadas exigem datas explícitas ou inclusão manual, com aviso visível.
- Cultos sem horário não recebem horário presumido; a interface sinaliza a pendência na agenda.
- Datas adicionais idênticas à ocorrência principal são deduplicadas por evento/data/horário.
- Uma ocorrência adicional pode ser criada somente no planejamento e opcionalmente referenciar um evento existente. Isso não cria um novo `Event` nem altera a agenda.
- Configurações específicas de `EventDate` prevalecem por equipe sobre as do evento-base. Uma configuração específica inativa retira aquela equipe da data adicional.

Cada equipe recebe uma cor estável, nome, quantidade e atividades. Atividades são responsabilidades compartilhadas pela composição da equipe, sem vagas independentes. Configurações e nomes são copiados no início do planejamento; retomar um rascunho não o recria a partir de configurações que podem ter mudado depois.

## Dados compartilhados

Migração aditiva: `0121_schedule_assistant_planning`, dependente de `0120_media_team_configuration`.

- `MonthlySchedule`: continua sendo o registro principal da escala, criado com `is_published=False`.
- `ScheduleDay`: continua sendo o dia canônico da escala, compartilhado pelas ocorrências daquela data.
- `ScheduleAssistantState`: extensão 1:1 da escala; período, etapa atual, versão, autoria, estado e metadados de revisão de disponibilidades.
- `ScheduleOccurrence`: detalha um culto naquele dia, com horário, nome, notas, referências opcionais à agenda e identidade de importação estável.
- `ScheduleOccurrenceTeam`: participação planejada da equipe; quantidade, atividades, política de acompanhamento, observações e divisão canônica associada para aquela ocorrência.
- `DivisionMember`: continua sendo a designação canônica e recebe a referência opcional à participação, a origem, o bloqueio e o nível de experiência fotografado. Assim, administração, impressão e histórico continuam lendo a mesma escala.

As novas entidades possuem histórico explícito e exclusão lógica. A mudança de equipe, atividade ou recorrência permanente não reescreve os snapshots já planejados. O cadastro de `Member`, equipes organizacionais, equipes de rotação `Team` e atribuições `DivisionMember` permanece compartilhado e sem conversões.

Retirar uma ocorrência exclui logicamente apenas seu planejamento e participações; o evento original da agenda permanece. Reimportá-la restaura sua identidade e composição preservada. Restaurar um dia que foi excluído na Administração não reativa indiscriminadamente suas demais ocorrências.

## API e persistência

Rotas principais:

- `GET /media/secretaria/`
- `GET /media/secretaria/escalas/`
- `POST /media/secretaria/escalas/automatica/` — formulário de mês/ano; criação idempotente por período.
- `GET /media/secretaria/escalas/assistente/<id>/` — interface.
- `GET|POST /media/secretaria/escalas/assistente/<id>/dados/` — estado e mutações.

Toda mutação JSON exige `version` obtida do estado anterior, além de CSRF. Ações:

- `add_source`: referência a uma ocorrência retornada no catálogo da agenda.
- `add_manual`: nome, data, horário, notas e `event_id` opcional.
- `save_occurrence`: ocorrência, campos locais e lista explícita de equipes. `apply_to` aplica somente a composição das equipes a outras ocorrências do mesmo rascunho; nunca troca suas datas/horários.
- `remove_occurrence`: retira a ocorrência do planejamento.
- `navigate`: salva a etapa atual, de 1 a 3.
- `generate`: exige confirmação e recalcula somente designações não bloqueadas.
- `set_assignments`: confirma uma composição manual e seus bloqueios, após validar todas as regras obrigatórias.
- `remove_obsolete_assignment`: remove uma designação cuja ocorrência ou equipe deixou o planejamento.
- `discard`: exige `confirmed: true` e descarta a escala canônica do rascunho por exclusão lógica.

As validações incluem mês, limites de texto, horários locais, quantidade positiva, equipe e atividades pertencentes ao contexto e IDs de destinos em lote. Falhas em qualquer parte revertem a transação inteira.

O salvamento automático é serializado no navegador, com debounce, feedback discreto, aviso ao sair com alterações pendentes e tentativa explícita de recuperação após falha. Valores digitados não são substituídos por uma resposta antiga. Datas/eventos já incluídos ficam identificados no seletor.

No servidor, bloqueios na ordem escala → estado serializam as mutações. A criação bloqueia o ministério e tem restrição de unicidade para um rascunho ativo por período. A versão inclui a revisão e a atualização do estado, metadados da escala e atualizações/exclusões dos dias canônicos. Edições administrativas previamente gravadas também invalidam uma versão antiga. O servidor responde **409**, sem aplicar a mutação; a interface mantém o formulário e solicita recarga explícita. Páginas e JSON do assistente não são cacheados.

Descartar exige confirmação na interface. A ação preserva agenda e configurações permanentes. Uma nova tentativa para o mesmo mês pode criar outro rascunho depois do descarte; os registros antigos permanecem na exclusão lógica/histórico.

## Visibilidade e publicação

As consultas de membros usam `member_visible_schedules()`. Escalas legadas mantêm o comportamento anterior; escalas do Assistente somente serão visíveis quando houver publicação explícita, incluindo o estado do fluxo e `is_published`.

Os acessos diretos ao detalhe do membro e à impressão também verificam essa condição. Administradores e a liderança autorizada podem inspecionar o rascunho, identificado como tal. A API não possui ação de publicação. As designações criadas na etapa 3 continuam invisíveis aos membros até a futura publicação explícita.

## Integração com disponibilidades

A etapa 2 está implementada em `services/schedule_availability.py`. O marcador `availability_revision` registra a última gravação. As respostas são armazenadas por escala, equipe, membro e ocorrência, com contexto de data/horário e identidade da participação. Novas ocorrências ou horários alterados ficam desconhecidos até revisão explícita; confirmações válidas e seleções originais são preservadas. Os indicadores globais e por ocorrência são reconciliados após as mutações. Detalhes e contrato de elegibilidade para o gerador estão em `docs/schedule_availability.md`.

## Implantação e validação

```sh
python manage.py migrate
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test website.test_schedule_assistant website.test_schedule_availability website.test_schedule_generation
node --test static/tests/schedule_assistant.test.cjs static/tests/schedule_availability.test.cjs static/tests/schedule_generation.test.cjs
```

A migração foi exercitada em bancos descartáveis; não foi aplicada ao banco da aplicação. Para publicar os novos assets, utilizar o `collectstatic` do fluxo de deploy existente. O ministério deve estar identificado por `midia_externa` (mantido o fallback pelo nome), com cultos/horários e configurações de equipes cadastrados.

Testes cobrem retomada idempotente, mês completo, recorrências e datas adicionais, cultos distintos no mesmo dia, snapshots, importação sem duplicar eventos, inclusão local com referência opcional, mudanças pontuais/em lote, rollback de edição inválida, isolamento de ministérios, permissões, CSRF, descarte, acesso direto a rascunhos, disponibilidades, geração determinística, acompanhamento, bloqueios, regeneração e ausência de publicação. Dois testes com conexões concorrentes no PostgreSQL verificam criação única e rejeição de escrita com versão antiga.

A interface foi exercitada no Chrome, em desktop e viewport móvel, incluindo quantidade salva automaticamente, recarga/restauração, evento da agenda, inclusão de equipe, navegação preparada e conflito entre duas abas. Os testes automatizados do calendário verificam limites do mês, ano e fevereiro bissexto.

Resultado final no PostgreSQL 17 (UTF-8), Django 4.2.16 e Python 3.13: **31 testes do Assistente e 20 do cadastro de equipes aprovados**, incluindo os dois testes concorrentes. Os 3 testes JavaScript também passaram. Na rodada ampliada, 180 de 191 testes passaram; as 9 falhas e 2 erros restantes são exatamente os 11 problemas preexistentes já documentados em `docs/media_team_configuration.md` e reproduzidos na etapa anterior. `check`, `makemigrations --check --dry-run` e `git diff --check` passaram.

O módulo compartilhado de participação da Mídia foi extraído para `services/ministry_organization.py` para evitar um ciclo de importação entre as views genéricas de organização e o pacote de planejamento. As regras de acesso foram mantidas.

## Arquivos desta etapa

Novos:

- `website/models/schedule_assistant.py`
- `website/services/schedule_agenda.py`
- `website/services/schedule_assistant.py`
- `website/services/schedule_visibility.py`
- `website/views/schedule_assistant.py`
- `website/migrations/0121_schedule_assistant_planning.py`
- `website/test_schedule_assistant.py`
- `templates/member/schedule_assistant/{secretariat,list,wizard}.html`
- `static/schedule_assistant.{css,js}`
- `static/tests/schedule_assistant.test.cjs`
- `docs/schedule_assistant.md`

Integrações:

- `website/models/__init__.py` e `website/urls.py`
- `website/services/ministry_organization.py`
- `website/views/{member_auth,member_schedule,ministry_organization}.py`
- `website/views/media_planning/mixins.py`
- `website/views/schedules/monthly.py`
- `templates/member/dashboard.html`
- `templates/member/media_planning/organization_dashboard.html`
- `templates/admin_panel/schedules/monthly/{detail,print}.html`

Os arquivos da etapa anterior de cadastro de equipes foram preservados; a migração 0120 continua sendo pré-requisito.
# Geração e revisão da escala

## Etapa 3

A etapa de geração grava sugestões no mesmo `MonthlySchedule` e nas mesmas
`DivisionMember` que a Administração Geral já usa. Cada sugestão aponta para
`ScheduleOccurrenceTeam`, preservando a ocorrência (data e horário), a equipe,
o nível de experiência fotografado no momento da sugestão, a origem e o bloqueio
manual. Não há uma tabela paralela de escala.

`website.services.schedule_optimizer` é um modelo CP-SAT determinístico do
OR-Tools: um trabalhador, semente 0 e orçamento determinístico por objetivo.
O serviço não conhece Django nem faz escrita. `schedule_generation` prepara os
dados, valida, persiste e serializa a revisão.

As restrições duras são disponibilidade confirmada, vínculo ativo, experiência
classificada, um culto por pessoa a cada dia, compromissos publicados e bloqueios
manuais. Para duas vagas, há sempre um apto a acompanhar. Para outro total, a
secretaria deve configurar o mínimo de aptos; sem essa política, a atuação fica
pendente e não recebe sugestões.

Os objetivos lexicográficos são, nesta ordem: vagas válidas preenchidas; carga
total; carga de aptos; atuações consecutivas da mesma equipe (inclui o limite do
mês anterior); duplas consecutivas repetidas; oportunidades de treinamento; e
concentração entre equipes. Cada ótimo é fixado antes da próxima prioridade.
Quando uma prioridade excede seu limite, a melhor solução válida já encontrada
é preservada e a tela informa que não houve otimização completa.

Alterações manuais são validadas no servidor e ficam bloqueadas por padrão. Ao
regenerar, somente designações desbloqueadas são recalculadas. Mudanças no
planejamento, disponibilidade, vínculo, experiência ou compromissos tornam a
geração identificável como desatualizada; alterações concorrentes retornam 409.
Esta etapa não chama `publish()` e não torna nenhuma escala visível aos membros.

