# Eventos, demandas e cronograma

Segunda etapa da Organização da Mídia. Evolui os modelos existentes `Event`,
`MediaContent`, `MediaTask`, `MediaEventType`, `MediaPlanningTemplate` e
`MediaPlanningTemplateItem`; reutiliza equipes e permissões da primeira etapa.

## Uso

- **Eventos** (`/media/events/`): lista com quantidade e progresso das demandas,
  acesso ao detalhe e criação simples com prévia do template.
- **Demandas** (`/media/demands/`): lista única de demandas vinculadas a eventos
  e avulsas. Filtros recolhíveis de evento, equipe, responsável (incluindo etapas),
  status, prioridade e prazo.
- **Cronograma** (`/media/calendar/`): semana ou mês, usando as mesmas entidades.
  Exibe eventos, início previsto, prazo da demanda, publicação e etapas pendentes.
- **Tipos de evento**: criar o tipo e adicionar suas demandas padrão. Cada item
  aceita equipe sugerida e prazos em dias antes/no dia/depois. Edição e criação
  usam o mesmo controle. Arquivar impede novos usos e preserva os vínculos antigos.
- **Editar evento e datas**, no detalhe: altera início e término opcionais,
  descrição e local. Exibe quantos prazos automáticos e manuais existem.
- **Prazos e detalhes**, na demanda: permite alterar datas, prioridade, status,
  equipe e responsável com as validações existentes.

Os membros continuam podendo criar eventos/demandas pelo fluxo rápido. Edições,
configuração de templates e arquivamento continuam restritos aos líderes.

## Regras de datas

A geração copia os offsets de início, entrega e publicação para a demanda, junto
com indicadores independentes de data automática. `0` significa no dia do evento;
`NULL` significa sem data. São usados dias corridos e meio-dia no fuso configurado,
seguindo o serviço de datas existente.

Alterar o template não modifica demandas já geradas, nem as regras copiadas.
Alterar `Event.event_date` por `save()` recalcula apenas datas automáticas, dentro
da mesma transação do evento. Isso também cobre a edição administrativa existente.
Alterar ou apagar uma data da demanda a torna manual. Mudar ou remover seu evento
mantém as datas e desliga o vínculo automático. Não há sincronização retroativa
implícita com o template ou restauração automática de um prazo manual.

Etapas mantêm seus próprios prazos. Sua edição não sobrescreve um prazo já definido
na demanda. Para compatibilidade, uma demanda sem prazo ainda pode receber o
primeiro prazo das etapas no fluxo rápido.

A geração bloqueia a linha do evento no PostgreSQL e ignora itens já aplicados,
inclusive demandas removidas, para impedir duplicação por reaplicação/concorrência.
Não elimina duplicatas históricas. Novas equipes sugeridas devem estar ativas e
pertencer ao ministério de mídia existente. Responsável individual começa vazio.

## Migração e compatibilidade

`0104_media_event_deadlines` acrescenta campos opcionais em `Event` e `MediaContent`.
Não reescreve títulos, datas, responsáveis, status, equipes ou vínculos existentes.
Os indicadores automáticos começam falsos em registros antigos: não é possível
inferir com segurança se seu prazo havia sido personalizado.

Aplicar no ambiente normal do projeto após instalar as dependências da primeira
etapa e fazer o backup habitual:

```sh
python manage.py migrate
python manage.py check
```

As datas `display_start`/`display_end` continuam sendo exclusivamente o período de
exibição pública. O término do evento tem campos próprios. Recorrências,
`EventDate`, planos mensais, comentários, anexos e URLs existentes permanecem.
O status resumido do evento é derivado do progresso; não foi criado novo workflow.

Os planos mensais continuam sendo associações editoriais existentes: mover um
evento não altera automaticamente sua seleção no plano. Tipos de evento seguem
no contexto do módulo de mídia; não foi criado outro sistema de ministérios.

Não usar `QuerySet.update(event_date=...)` nem atualização SQL direta para
reagendar eventos: essas operações não executam `Event.save()` e seu recálculo.
Da mesma forma, alterações manuais de datas de demandas devem passar por `save()`.

A personalização item a item antes da criação é uma evolução opcional: nesta
etapa há prévia, opção de aplicar o template completo e edição independente após
a criação. O serviço já aceita uma seleção de itens. A sugestão de manuais foi
mantida como evolução futura, reutilizando `MinistryManual` quando implementada.

## Verificação

Testes em `website/test_media_operations.py`: offsets antes/no dia/depois,
independência do template, prazos manuais, recálculo, reaplicação, arquivamento,
permissões, filtros, cronograma, formulários e preservação na migration.
A suíte de regressão inclui também a primeira etapa e permissões de mídia externa.

A validação de navegador cobre prévia, geração, recálculo, listagens e cronograma
em desktop/celular. Não substitui homologação com os dados reais da igreja.
