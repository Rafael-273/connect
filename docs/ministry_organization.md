# Organização dos ministérios

## Estrutura e decisões

O projeto usa Django, ModelForms, views por módulo e templates renderizados no
servidor. A evolução mantém essa arquitetura, os layouts do portal e as mensagens
Django. Não há nova API REST ou aplicação frontend separada.

- `Ministry` e `MinistryMembership` continuam sendo as fontes de ministério,
  participação e papel (`leader`/`member`). `Member.user` continua opcional.
- `MediaSubTeam` e `MediaSubTeamMembership` foram generalizados com ministério
  explícito. Os nomes técnicos e as tabelas foram mantidos para preservar IDs,
  referências, funções e demandas. Não representam mais uma restrição à Mídia.
- `Team`, de `models/schedule.py`, continua representando grupos de rotação em
  escalas. Não foi convertido nem mesclado com as subequipes de organização.
- `MediaContent.sub_team`, `responsible` e `MediaTask.assigned_to` são reutilizados.
  Sem equipe, o fluxo de atribuição individual continua disponível.
- A nova entidade `MinistryManual` herda `BaseModel`, com ministério obrigatório,
  equipe opcional, título, resumo, conteúdo, criador, timestamps e status ativo.
  Arquivar muda o status, sem destruir conteúdo.

## Acesso e navegação

O dashboard do membro tem a entrada **Meus ministérios**. A gestão administrativa
de membros do ministério também oferece **Equipes** e **Manuais**. As URLs antigas
`media/organizacao/equipes/…` encaminham aos mesmos registros e às views genéricas.

`/ministries/<ministry_id>/teams/` e `/ministries/<ministry_id>/manuals/` delimitam
as consultas pelo ministério. Participantes ativos podem ler conteúdo ativo;
líderes do próprio ministério e usuários autorizados pelo critério de
`StaffRequiredMixin` (`is_staff` ou `user_type=admin`) podem administrar.
Arquivados/inativos aparecem apenas para gestores. Usuários sem vínculo não
conseguem acessar outro ministério alterando IDs na URL ou no formulário.

## Integridade

- O nome da equipe é único dentro do ministério, e sua origem não pode ser trocada.
- Líderes e participantes da equipe devem participar ativamente do ministério.
- Um membro pode integrar várias equipes. Membros sem conta também podem integrar
  equipes, mas não são oferecidos como usuários responsáveis por demandas.
- Remover/desativar uma participação ministerial desativa suas participações nas
  subequipes e remove sua liderança dessas equipes. A remoção ministerial usa
  exclusão lógica e exige POST. Reativar o ministério não reinscreve equipes.
- As atribuições antigas são preservadas. A interface avisa sobre a saída, e as
  demandas exibem um aviso quando a equipe ou algum responsável deixa de ser
  elegível. Novas atribuições e edições exigem resolver a inconsistência.
- Formulários, modelos e sincronização de etapas validam equipe/responsáveis.
  O hub salva demanda e etapas em uma transação; IDs de etapas de outra demanda
  são rejeitados antes de alterar registros. Templates também são aplicados
  atomicamente e apresentam erros de equipe/função ao usuário.
- A desativação deve passar por `save()`/`delete()`; como outros hooks de modelos
  Django, os sinais não são executados por `QuerySet.update()` ou SQL direto.

As listas usam agregações e relações pré-carregadas. Os avisos de atribuição no
hub usam um mapa de participações consultado em lote.

## Editor e extensões

O editor usa um textarea com botões de formatação e pré-visualização autenticada.
O conteúdo é armazenado como texto Markdown e renderizado com `Markdown` e
`bleach`, permitindo títulos, negrito, itálico, listas, links, separadores,
citações e código. HTML fora da lista permitida, atributos de eventos e URLs
executáveis não são renderizados. O preview não salva o manual.

O CKEditor listado nas dependências não estava ativado e sua versão emite aviso
de segurança; ele não foi habilitado. Não foram adicionados uploads de imagens.

A ação de IA fica para uma extensão usando `website.ai.get_ai_service()`;
não foi criado outro provedor, cliente ou botão sem implementação. O manual tem
identidade própria e pode futuramente ser relacionado a tipos/templates de demanda
por uma FK/M2M, sem duplicar seu conteúdo.

## Migração e instalação

Instalar as dependências de `requirements.txt` e executar `python manage.py migrate`.
A migration `0103_ministry_organization`:

1. Adiciona temporariamente um Ministério nullable às subequipes.
2. Associa os registros legados, inclusive excluídos logicamente, ao ministério
   identificado pelo código `midia_externa` ou pelo nome histórico.
3. Interrompe com erro explícito se a identificação for ausente ou ambígua.
4. Torna o vínculo obrigatório, altera a unicidade e cria a tabela de manuais.

A identificação histórica só ocorre na migração e nas entradas legadas da Mídia.
As telas/regras genéricas recebem o ministério como contexto. Nenhuma equipe ou
demanda existente é recriada. Antes de reverter a migração após uso em vários
ministérios, é necessário considerar nomes de equipe repetidos e os novos manuais;
a antiga unicidade global não representa os dados novos. Mantenha um backup.

## Verificação

Testes do módulo: `python manage.py test website.test_ministry_organization`.
Incluem regras de vínculo, permissões, reativação, filtros, sanitização, preview,
atribuições, rollback, contagem de queries e migração com equipe/demanda legadas.

Também foram verificados permissões de Mídia Externa, serviço de IA, `check`,
`makemigrations --check --dry-run`, compilação Python, lint dos arquivos alterados,
JavaScript e `collectstatic`. O projeto não possui etapa de TypeScript/build Node.

A suíte antiga `website.tests` apresenta duas falhas também reproduzidas no HEAD
anterior: destino do redirect de Evangelismo (`/evangelism/list/` versus `/`) e
idioma da mensagem de campo obrigatório (inglês versus português). Não foram
alterados fluxos de Evangelismo para contornar essas falhas.

Validação desta entrega: 68 testes selecionados passaram no PostgreSQL, incluindo
os testes do módulo e as regressões de permissões de Mídia Externa/IA. A migração
foi aplicada ao PostgreSQL local depois de um `pg_dump`. O `collectstatic` com
WhiteNoise/manifesto processou os arquivos sem erros. O fluxo de criação,
pré-visualização e leitura de manuais, o layout mobile e a seleção de responsáveis
foram exercitados em Chrome sem erros de console. Dados de demonstração do navegador
foram criados somente em um SQLite temporário, separado do banco local da aplicação.
