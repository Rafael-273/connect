# Cadastro das Equipes da Mídia

## Uso

No dashboard, abra **Mídia Externa → Equipes → detalhes da equipe**.

- **Integrantes:** classificação por participação, com edição rápida e também no formulário de participação. Novos vínculos e vínculos existentes começam como `pending` (Pendente de classificação). `mentor` significa Apto a acompanhar e `trainee`, Em treinamento. O mesmo membro pode ter classificações diferentes em equipes diferentes.
- **Atividades:** cadastrar, editar, ordenar pelo número de ordem e desativar. São responsabilidades compartilhadas, sem criação de funções ou vagas individuais na escala. Nomes são únicos dentro da equipe; para reutilizar uma atividade desativada, edite e reative seu cadastro.
- **Cultos e atuações:** selecionar um evento da agenda com horário, ou uma de suas datas adicionais com horário. Eventos recorrentes mostram frequência, dia e hora. Cada atuação define quantidade de integrantes (inicialmente 2), atividades e situação. Uma equipe pode ter várias atuações e várias equipes podem atuar no mesmo culto.

Administradores e líderes ativos da Mídia mantêm as permissões existentes. As novas rotas recusam outros ministérios e IDs de equipes/atividades/participações que não pertençam ao contexto. Usuários administrativos sem perfil de membro também podem gerenciar os cadastros pelas rotas genéricas existentes.

## Dados e histórico

- `MediaSubTeamMembership.experience_level`: classificação no vínculo; não muda `Member`.
- `MediaTeamActivity`: atividade da subequipe, descrição, ordem e estado ativo.
- `MediaTeamService`: configuração da equipe para `Event` e, opcionalmente, `EventDate`, quantidade e estado ativo.
- `MediaTeamServiceActivity`: vínculos de atividades, com exclusão lógica e histórico. A propriedade `service.activities` retorna somente vínculos atuais, evitando a inclusão de vínculos removidos nos joins de modelos com exclusão lógica.
- Há histórico explícito nas novas entidades e no vínculo de participação. Alterações de papéis M2M da participação continuam com o comportamento anterior.

Nenhuma dessas operações escreve em `MonthlySchedule`, `ScheduleDay`, `ScaleDivision` ou `DivisionMember`. Não há conversão de `MediaSubTeam` em `Team`, duplicação de membros, geração automática ou publicação de escalas nesta entrega.

`MediaTeamService.snapshot()` prepara uma cópia dos valores de equipe, culto, data/hora, recorrência, quantidade e atividades ativas. Ao implementar o gerador, essa cópia deverá ser persistida junto à ocorrência gerada; uma referência ao cadastro mutável não substitui um snapshot histórico. O método sozinho não armazena uma revisão de escala.

A agenda continua sendo a origem dos cultos. `sync_event_dates()` conserva IDs de datas adicionais inalteradas nas edições administrativas e de Mídia. Uma data efetivamente removida/substituída segue o ciclo de exclusão lógica existente, assim como suas configurações dependentes, preservadas em `all_objects` e no histórico. Será preciso cadastrar a atuação na nova data. Exclusões definitivas da agenda mantêm o comportamento de cascade para configurações dependentes; não afetam escalas, que não possuem vínculo com esses novos cadastros.

## Migração e implantação

Migração: `0120_media_team_configuration`, após `0119_projectpreviewartifact`.

```sh
python manage.py migrate
python manage.py check
```

A migração adiciona a classificação com padrão pendente, as três entidades e suas tabelas de histórico, além de restrições de unicidade e quantidade positiva. Não cria atividades, atuações ou membros e não promove ninguém automaticamente a apto.

Aplicada e revertida nos bancos descartáveis dos testes. Não foi aplicada ao banco da aplicação. Reverter a migração após uso remove os novos dados; prefira reverter somente o código de apresentação se precisar interromper a funcionalidade sem perder cadastros.

## Antes de ativar o futuro gerador

1. Confirmar o ministério de Mídia (`code=midia_externa`; mantém-se o fallback histórico pelo nome).
2. Classificar as participações pendentes por equipe.
3. Conferir cultos, horários e padrões de recorrência na agenda.
4. Cadastrar e ordenar atividades; configurar as atuações e a quantidade de pessoas.
5. Implementar a expansão de ocorrências, disponibilidades, conflitos e publicação da etapa seguinte.
6. Na geração, considerar apenas equipe, ministério, participação e atuação válidos/ativos; exigir ao menos um `mentor` por grupo. `pending` não comprova aptidão. Várias atividades da atuação usam a mesma quantidade de integrantes.
7. Persistir os snapshots das configurações usadas e preservar ajustes manuais nas escalas oficiais.

## Validação

Suíte nova: `website.test_media_team_configuration`.

```sh
python manage.py test website.test_media_team_configuration
python manage.py test website.test_media_team_configuration website.test_ministry_organization website.test_media_operations website.test_media_work
python manage.py makemigrations --check --dry-run
```

Os testes novos cobrem migração de vínculos antigos para pendente, classificação por equipe, formulários legados, criação/ordenação/desativação de atividades, quantidade independente das atividades, cultos com horários distintos, datas adicionais, validações, edição rejeitada sem perda de dados, exclusão lógica de vínculos, snapshots, preservação de escalas, permissões, IDs de outras equipes, CSRF e usuário administrativo sem Member.

Validação executada em ambiente descartável com Django 4.2.16, Python 3.13 e PostgreSQL 17. A versão original (HEAD), extraída em diretório separado, foi executada com o mesmo ambiente para comparação. Também houve execução dos testes novos em SQLite. A interface foi conferida no Chrome com dados fictícios e banco local separado.

Resultado final: **20/20 testes novos aprovados**. Na rodada ampliada de 160 testes em PostgreSQL, 149 passaram; os 11 problemas são os mesmos da versão original (140 testes, 129 aprovados). `check`, `makemigrations --check --dry-run` e `git diff --check` passaram.

As três suítes existentes apresentam 9 falhas e 2 erros também na versão original, nestes testes:

- `MinistryDemandTests.test_invalid_quick_create_rejects_user_outside_ministry`
- `MinistryDemandTests.test_model_team_change_cannot_leave_incompatible_tasks`
- `MinistryDemandTests.test_regular_member_can_create_demand_but_not_manage_teams`
- `MinistryDemandTests.test_rejected_update_does_not_change_title_or_tasks`
- `MinistryDemandTests.test_update_can_change_team_and_reassign_existing_steps`
- `MinistryOrganizationTests.test_add_member_from_teams_modal_invalid_reopens_modal`
- `MinistryOrganizationTests.test_team_list_queries_do_not_grow_with_team_count`
- `MediaOperationsTests.test_member_can_edit_event_demand_assignments`
- `MediaOperationsTests.test_event_demand_edit_uses_absolute_task_dates`
- `MediaOperationsTests.test_event_edit_modal_shows_and_updates_additional_dates`
- `MediaOperationsTests.test_template_item_steps_apply_as_tasks_in_form_order`

## Arquivos da entrega

Backend:

- `website/models/media_organization.py`
- `website/models/media_team_service.py`
- `website/models/__init__.py`
- `website/forms/media_organization.py`
- `website/views/media_team_configuration.py`
- `website/views/ministry_organization.py`
- `website/urls.py`
- `website/services/event_dates.py`
- `website/views/admin_panel/events.py`
- `website/views/media_planning/operations.py`

Interface:

- `templates/member/ministry_organization/team.html`
- `templates/member/ministry_organization/teams.html`
- `templates/member/ministry_organization/team_add_member_modal.html`
- `templates/member/ministry_organization/team_default_form.html`
- `templates/member/ministry_organization/team_defaults.html`

Migração, testes e documentação:

- `website/migrations/0120_media_team_configuration.py`
- `website/test_media_team_configuration.py`
- `docs/media_team_configuration.md`
