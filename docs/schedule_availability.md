# Assistente de Escalas — disponibilidades

## Fluxo entregue

Na Secretaria da Mídia, abra um rascunho do Assistente e selecione a etapa **Disponibilidades**.

1. Selecione a equipe e um integrante já vinculado a ela. A busca, a classificação de experiência e as outras equipes da Mídia aparecem na lista e no cabeçalho da pessoa.
2. Escolha explicitamente um modo. Para uma pessoa sem registro, nenhum modo é presumido.
3. Selecione ocorrências individualmente ou por dia, usando os atalhos de seleção. Cultos da manhã e da noite do mesmo dia possuem controles e horários distintos.
4. Confira o resumo textual e salve. O formulário pode ser reaberto com o modo, as exceções e as observações já preenchidos.

O cadastro é feito exclusivamente por usuários já autorizados na Secretaria (liderança ativa da Mídia e administradores). Não há rota de preenchimento por voluntários, conexão com o WhatsApp ou criação de novos membros. Integrantes sem conta de usuário também podem ter disponibilidade registrada pela secretaria.

Modos:

- `all`: disponível para todas as ocorrências atuais da equipe.
- `only`: disponível apenas nas selecionadas; as demais são confirmadas como indisponíveis.
- `except`: disponível em todas, menos nas selecionadas.

`only` sem seleção é uma declaração explícita de indisponibilidade em todas as ocorrências, descrita assim no resumo antes de salvar. `except` sem exceções confirma todas, preservando o modo original. Sem registro, todas são **desconhecidas**, não indisponíveis e não disponíveis.

É possível marcar como não informada uma declaração existente. Essa ação afeta somente a pessoa, a equipe e o rascunho selecionados, preservando o histórico.

## Dados e representação inequívoca

Migração aditiva: **0122_schedule_availability**, dependente de 0121.

- `ScheduleAvailability`: FK para a `MonthlySchedule` canônica, membro e subequipe existentes; modo, IDs originais das seleções/exceções, observações, responsável pelo registro e data de registro. Uma declaração ativa por escala/equipe/membro.
- `ScheduleAvailabilityOccurrence`: confirmação explícita por ocorrência, com `is_available`, identidade da participação da equipe e cópias da data, horário e nome. Uma confirmação ativa por declaração/ocorrência.

As disponibilidades não dependem de `DivisionMember` nem de a pessoa ter sido escalada. Os novos modelos possuem histórico explícito e exclusão lógica. Nenhuma migração cria declarações, promove classificações de experiência ou reaproveita dados de outro mês. O escopo é o ID da escala; outro mês ou outro rascunho inicia desconhecido.

A seleção original é preservada independentemente do resultado da futura geração. A confirmação guarda o contexto em que a secretaria informou a disponibilidade. O estado efetivo é calculado por `confirmation_status()`:

- **Disponível:** confirmação positiva compatível com a ocorrência, a participação da equipe e o modo/exceções atuais.
- **Indisponível:** confirmação negativa válida no mesmo contexto.
- **Desconhecido:** ausência de registro/confirmação, data ou horário alterados, participação de equipe substituída ou confirmação antiga incompatível com uma declaração posterior.

Não se deve filtrar apenas o campo bruto `is_available` para gerar escalas: uma confirmação histórica pode ter deixado de ser válida. O contrato de elegibilidade é `available_candidates()`.

A estrutura não possui conceitos específicos de tela ou WhatsApp. Uma futura Área do Membro poderá reutilizar o domínio com autorização e escopo próprios; o acesso atual permanece exclusivo da Secretaria.

## Mudanças no planejamento

Ao adicionar uma ocorrência depois do cadastro, ela fica desconhecida, inclusive para quem havia escolhido `all`. Mudanças de data/horário e remoção/reinclusão de uma equipe podem invalidar somente os contextos afetados. Alterar observações ou quantidade de vagas não apaga janelas de disponibilidade ainda válidas.

As seleções de ocorrências removidas continuam armazenadas e aparecem identificadas fora do planejamento atual. Restaurar a mesma ocorrência com a mesma participação, horário e intenção declarada pode reutilizar uma confirmação ainda válida. Se a declaração mudou enquanto a ocorrência estava removida e a confirmação antiga contradiz o modo atual, a ocorrência restaurada fica desconhecida.

Para confirmar novamente contextos a revisar, a secretaria precisa marcar **“Confirmo que revisei as ocorrências e os horários atuais”**. O backend exige `confirm_current_plan: true`; o modo `all` não confirma novas datas silenciosamente. O salvamento mantém os IDs da declaração e das confirmações válidas.

A integração utiliza `availability_revision` como revisão do último registro e recalcula os avisos de revisão. A lista global do rascunho e os indicadores por integrante/ocorrência são atualizados. Confirmar um integrante não resolve pendências de outros.

## Resumo e regra de um culto por dia

O resumo conta pessoas únicas, sem duplicá-las entre equipes:

- integrantes participantes do planejamento;
- pessoas com pelo menos um registro;
- pessoas com informação completa em todas as suas equipes/ocorrências relevantes;
- pessoas ainda pendentes, por ausência de registro ou necessidade de revisão.

“Com registro” e “pendentes” podem se sobrepor: por exemplo, Maria informou Cobertura, mas ainda não informou Live.

Por culto/equipe, são mostrados quantidade necessária, disponíveis, indisponíveis, desconhecidos e aptos a acompanhar com disponibilidade confirmada. Há avisos de quantidade insuficiente, ausência de acompanhamento e classificação de experiência pendente entre os disponíveis. `pending` e `trainee` nunca são contados como aptos a acompanhar.

A análise diária conta pessoas e mentores únicos e sinaliza insuficiência sob a regra de no máximo um culto por pessoa/dia. Os mínimos diários são limites necessários, não uma certificação de que as equipes são viáveis: utiliza-se o maior grupo necessário em cada culto e somam-se os cultos diferentes do dia. As contagens por equipe podem compartilhar candidatos. Não há alocação provisória ou promessa de escala completa.

Informar disponibilidade para dois cultos no mesmo dia é permitido, porque representa possibilidades, não duas designações. O contrato do futuro gerador impõe a restrição na seleção:

```python
candidates = available_candidates(state, team_plan, assigned_occurrences_by_member)
validate_daily_service_limit(target_occurrence, assigned_occurrences)
```

`available_candidates()` exclui desconhecidos, indisponíveis, membros/vínculos/equipes inativos e ocorrências canceladas/removidas; também retira quem já foi selecionado para outro culto no mesmo dia, conforme os compromissos fornecidos. A futura geração deve fornecer todos os compromissos relevantes e usar esse contrato ao adicionar cada pessoa. A verificação usa o `Member` global, não o vínculo de equipe. Vínculos diferentes não contornam a regra. Não foram alteradas as operações de escalas de outros ministérios.

A interface permite avançar com avisos após confirmação. A navegação não transforma pendências em disponibilidade nem publica a escala. A geração continua reservada à etapa seguinte.

## API e concorrência

São utilizadas as mesmas rotas e autorizações do rascunho, em `GET|POST /media/secretaria/escalas/assistente/<id>/dados/`.

O GET passa a incluir `availability.roster`, `availability.summary` e `availability.review_occurrence_ids`.

Gravação:

```json
{
  "action": "save_availability",
  "version": "versão retornada no GET",
  "team_id": 1,
  "member_id": 10,
  "mode": "except",
  "selected_occurrence_ids": [42],
  "notes": "Informação recebida pela secretaria",
  "confirm_current_plan": false
}
```

Reset para desconhecido:

```json
{
  "action": "clear_availability",
  "version": "versão retornada no GET",
  "team_id": 1,
  "member_id": 10
}
```

CSRF, versão otimista, bloqueios da escala/estado e transação são compartilhados com o planejamento. Uma versão antiga resulta em 409, sem sobrescrever dados. Validam-se vínculo ativo com equipe e ministério, mês/escala das ocorrências, modo, seleções, confirmação de revisão e limites de texto. IDs grandes de membros/ocorrências são aceitos; o limite de quantidade de integrantes não é aplicado aos identificadores.

É necessário existir pelo menos uma ocorrência planejada para a equipe antes de confirmar a disponibilidade. Depois da publicação ou do descarte, o endpoint de edição do rascunho não aceita alterações. Tentativas de autoatendimento por usuários comuns são negadas.

As disponibilidades têm salvamento explícito, precedido de resumo. Ao navegar entre etapas, alterações válidas pendentes são salvas. Trocar de pessoa/equipe com alterações não salvas pede confirmação de descarte. Falhas preservam o formulário, e a repetição de uma requisição mantém seu destinatário original, mesmo que a seleção visual tenha mudado.

## Migração e testes

```sh
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test website.test_schedule_availability website.test_schedule_assistant website.test_media_team_configuration
node --test static/tests/schedule_availability.test.cjs static/tests/schedule_assistant.test.cjs
```

A migração foi aplicada apenas a bancos descartáveis de validação. O banco da aplicação não foi alterado. Não há nova dependência de produção.

Os testes cobrem os três modos, não informado versus indisponível, horários distintos no mesmo dia, persistência e auditoria, edição sem duplicação, equipes e meses independentes, alterações/restauração do planejamento, confirmação de revisão, candidatos elegíveis, limite diário, capacidade e experiência, vínculos inativos, permissões, CSRF, escopos inválidos, publicação/descarte, IDs grandes, consultas em quantidade estável, migração e duas gravações concorrentes com a mesma versão no PostgreSQL.

Resultado final em Django 4.2.16, Python 3.13 e PostgreSQL 17 UTF-8: **38 testes novos de backend aprovados**, junto aos 51 testes do Assistente e cadastro de equipes das etapas anteriores. Os **8 testes JavaScript** passaram. Na rodada ampliada, **218 de 229 testes passaram**; as 9 falhas e 2 erros restantes são exatamente os 11 problemas preexistentes reproduzidos nas etapas anteriores e documentados em `docs/media_team_configuration.md`. A primeira rodada também foi executada em SQLite, com os testes de bloqueio concorrente reservados ao PostgreSQL. `check`, `makemigrations --check --dry-run` e `git diff --check` passaram.

A interface foi conferida no Chrome, em desktop e tela móvel, com dados fictícios: modo de exceções, culto noturno separado do matutino, salvamento/restauração, ausência de cópia entre equipes e revisão após alterar o horário de uma ocorrência.

## Arquivos desta etapa

Novos:

- `website/models/schedule_availability.py`
- `website/services/schedule_availability.py`
- `website/migrations/0122_schedule_availability.py`
- `website/test_schedule_availability.py`
- `templates/member/schedule_assistant/availability.html`
- `static/schedule_availability.js`
- `static/schedule_availability.css`
- `static/tests/schedule_availability.test.cjs`
- `docs/schedule_availability.md`

Integrações:

- `website/models/__init__.py`
- `website/models/schedule_assistant.py` (descrição do marcador de revisão)
- `website/services/schedule_assistant.py`
- `templates/member/schedule_assistant/wizard.html`
- `static/schedule_assistant.js`
- `docs/schedule_assistant.md`

Os arquivos e dados das etapas anteriores foram preservados.
