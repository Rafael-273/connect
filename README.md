# Connect — Plataforma de Gestão da Igreja Filadélfia

Sistema web completo de gestão eclesiástica desenvolvido para a Igreja Filadélfia. Gerencia membros, visitantes, evangelismo, consolidação de novos convertidos, ministérios, escala de culto, cursos de frequência, palavras de conhecimento, testemunhos, aba de cantina, tradução simultânea em tempo real e um site público.

---

## Índice

- [Tecnologias](#tecnologias)
- [Funcionalidades](#funcionalidades)
- [Arquitetura](#arquitetura)
- [Modelos de Dados](#modelos-de-dados)
- [URLs e Views](#urls-e-views)
- [Autenticação e Permissões](#autenticação-e-permissões)
- [WebSockets e Tradução em Tempo Real](#websockets-e-tradução-em-tempo-real)
- [Arquivos Estáticos e Mídia](#arquivos-estáticos-e-mídia)
- [Variáveis de Ambiente](#variáveis-de-ambiente)
- [Serviços Externos](#serviços-externos)
- [Como Rodar Localmente](#como-rodar-localmente)
- [Deploy](#deploy)
- [Comandos de Gerenciamento](#comandos-de-gerenciamento)
- [Migrações](#migrações)
- [Painel Administrativo](#painel-administrativo)

---

## Tecnologias

| Componente | Versão / Pacote |
|---|---|
| **Python** | 3.10.5 |
| **Django** | 4.2.16 |
| **Servidor ASGI** | Daphne 4.0.0 |
| **Servidor WSGI** | Gunicorn 23.0.0 |
| **WebSockets** | Django Channels 4.0.0 + channels-redis 4.2.0 |
| **Banco de dados** | PostgreSQL (psycopg2-binary 2.9.9) |
| **Cache / filas / Channel Layer** | Redis + Celery |
| **Arquivos estáticos** | WhiteNoise 6.8.2 (comprimido + manifesto) |
| **Armazenamento de mídia** | Filesystem local ou AWS S3 (django-storages + boto3) |
| **Soft-delete** | django-safedelete 1.4.0 |
| **Histórico / auditoria** | django-simple-history 3.7.0 |
| **Editor rich text** | django-ckeditor 6.7.2 |
| **Geração de PDF** | xhtml2pdf 0.2.16 + reportlab 4.0.7 |
| **Speech-to-text** | azure-cognitiveservices-speech 1.25.0 |
| **IA / transcrição de arquivos** | OpenAI Responses API + Audio Transcriptions API |
| **Tradução** | googletrans 4.0.2 (ao vivo) + OpenAI (legendas) |
| **Secrets** | Azure Key Vault (azure-keyvault-secrets 4.9.0) |
| **Widgets de select** | django-select2 8.2.1 |

---

## Funcionalidades

### Portal do Membro
- Dashboard pessoal com acesso rápido às funcionalidades
- Perfil do membro (foto, dados pessoais, bairro, cônjuge)
- Visualização da escala mensal do ministério
- Roteiro do culto (boletim da semana)
- Envio de palavras de conhecimento pré-culto
- Acompanhamento do processo de consolidação

### Gestão de Membros e Visitantes
- Cadastro de novos membros e visitantes
- Registro de evangelizados (com indicação de desejo de visita)
- Listagem de novos convertidos
- Histórico completo com auditoria de alterações (soft-delete)

### Consolidação / Acompanhamento
- Templates de acompanhamento (ex.: 4 ou 8 semanas) com etapas semanais configuráveis
- Atribuição de responsáveis por convertido
- Registro semanal de visitas e progresso em percentual

### Ministérios e Escalas
- CRUD completo de ministérios, equipes e membros
- Criação de escalas mensais com dias e turnos
- Verificação de conflitos de escala
- Visualização para impressão

### Cursos de Frequência
- Cadastro de cursos com recorrência (semanal, quinzenal ou personalizada)
- Registro de presença por aula
- Exportação de relatórios em PDF

### Palavras de Conhecimento
- Membros submetem palavras antes do culto
- Aprovadores validam as palavras
- Tela de serviço exibe somente as aprovadas
- Registro de curas associadas

### Testemunhos
- Submissão por membros ou criação pelo admin
- Fluxo de aprovação antes da exibição pública
- Seleção de testemunhos para a página inicial

### Roteiro do Culto
- Anúncios com datas múltiplas e expiração automática
- Reordenação de itens
- Versão para impressão

### Cantina
- Controle de anotações (aba) com valor e status de pagamento

### Tradução Simultânea em Tempo Real
- Captura de áudio via microfone no navegador
- Reconhecimento de fala em PT-BR via Azure Cognitive Services
- Tradução simultânea para Inglês e Holandês via Google Translate
- Transmissão ao vivo para espectadores via WebSocket

### Mídia Externa
- Acesso exclusivo aos membros do ministério com código `midia_externa`
- Arquitetura `Template → Versão → Projeto → Pipeline`, com projetos antigos presos à versão usada
- Templates, blocos, plugins, arquivos padrão, LUT, música, formato e legenda configurados no Painel Administrativo
- Projetos guiados na Área de Membros, uploads por bloco, duplicação e histórico
- Extração e divisão de áudio com FFmpeg para suportar cultos longos
- Transcrição com `whisper-1`, incluindo timestamps de palavras e geração de blocos sincronizados
- Tradução idiomática por IA com glossário fixo da igreja e validação rígida por `cue_id`
- Editor que permite alterar texto e quebras de linha, nunca timestamps
- Renderização assíncrona de vídeos PT/EN e downloads privados em MP4, SRT e VTT
- Presets configuráveis para YouTube, Reels, Stories, Feed, TikTok e Telão
- Auto Reframe com OpenCV, prioridade em rosto ou corpo, múltiplas pessoas e movimento suavizado
- Enquadramento `cover` em toda saída, sem barras pretas ou áreas vazias
- Progresso automático por polling e processamento em worker Celery
- Templates iniciais: Tradução de Vídeo, Stories da Pregação, Reels e Anúncio Mensal

Os plugins de legenda, tradução, montagem de blocos, intro/outro, LUT, música e
Auto Reframe possuem execução local. Corte inteligente de silêncio e remoção de
vícios de fala têm contratos de serviço próprios, mas ainda exigem um processador
especializado no worker; enquanto não configurados, a etapa fica marcada como
ignorada no histórico do projeto.

---

## Arquitetura

```
connect/               # Configuração do projeto Django
├── settings.py        # Configurações globais
├── urls.py            # Roteamento raiz (monta /admin/ e website/)
├── asgi.py            # Entrada ASGI com ProtocolTypeRouter (HTTP + WS)
└── wsgi.py            # Entrada WSGI

website/               # App principal
├── ai/                # Gateway compartilhado de IA e transcrição de arquivos
├── models/            # Todos os modelos de domínio
├── views/             # Views organizadas por módulo
├── forms/             # Formulários Django
├── templatetags/      # Tags e filtros customizados
├── consumers.py       # WebSocket consumers (áudio + transcrição)
├── routing.py         # Rotas WebSocket
├── middleware.py      # Middleware de limpeza de mensagens flash
├── migrations/        # 40 migrações (0001–0040)
└── management/        # Comandos de gerenciamento customizados

templates/             # Templates HTML
├── base.html          # Base do site público
├── member_base.html   # Base do portal do membro
├── admin_base.html    # Base do painel administrativo
├── front/             # Páginas públicas
├── member/            # Páginas do portal do membro
├── admin_panel/       # Páginas do painel admin
├── words/             # Palavras de conhecimento
├── parciais/          # Partials reutilizáveis
└── password_reset/    # Redefinição de senha

static/                # Arquivos estáticos de desenvolvimento
staticfiles/           # Arquivos coletados (produção, com versão e brotli)
media/                 # Uploads (avatares, fotos, banners)
```

---

## Modelos de Dados

Todos os modelos de domínio herdam de **`BaseModel`** (`website/models/_base.py`), que inclui:
- `SafeDeleteModel` — soft-delete com cascade
- `HistoricalRecords` — trilha de auditoria completa
- Campos `created_at` e `update_at`

| Modelo | Descrição |
|---|---|
| `User` | Usuário customizado (`AbstractBaseUser`); login por e-mail; `user_type`: `member`, `admin`, `visitors`, `consolidation`, `events` |
| `Member` | OneToOne → `User`; FK → `Neighborhood`; auto-referência → `spouse`; flags `is_approver`, `is_consolidated`, `is_discipled` |
| `Visitor` | FK → `Neighborhood`; `decision_for_jesus`, `wants_home_prayer` |
| `Evangelized` | FK → `Member` (quem evangelizou); FK → `Neighborhood`; `wants_peace_house` |
| `Neighborhood` | Auto-referência → `parent` (hierarquia de bairros) |
| `Ministry` | nome, cor, `is_active` |
| `MinistryMembership` | FK → `Member`, FK → `Ministry`; papel: `leader` / `member` |
| `FollowUpTemplate` | Template de consolidação com semanas configuráveis |
| `FollowUpTemplateStep` | FK → `FollowUpTemplate`; número da semana, título e descrição |
| `FollowUp` | FK → `Member` (acompanhado), FK → `Member` (responsável), FK → `FollowUpTemplate` |
| `Event` | Banner, data/hora, janela de exibição, suporte a recorrência, slug |
| `WordOfKnowledge` | FK → `Member`; tipo de culto (quarta/domingo); fluxo de aprovação |
| `Testimony` | FK → `Member`; categoria; `is_approved`, `show_on_home` |
| `Music` | nome, cantor, arquivo de cifra, andamento |
| `Team` | FK → `Ministry`; M2M → `Member`; FK → líder `Member` |
| `MonthlySchedule` | FK → `Ministry`; escala mensal com equipes e dias |
| `ScheduleDay` | FK → `MonthlySchedule`; atribuições de turno/dia |
| `AttendanceCourse` | Cursos com regras de recorrência |
| `CourseLesson` | FK → `AttendanceCourse`; flag `was_held` |
| `CourseParticipant` | FK → `AttendanceCourse` + `Member` |
| `CanteenDebtor` | Nome, valor, flag `paid` |
| `Roteiro` | Script único global do culto |
| `AnuncioRoteiro` | FK → `Roteiro`; anúncios com datas de expiração |
| `PrayerRequest` | Pedidos de oração anônimos ou identificados |

---

## URLs e Views

`connect/urls.py` monta `/admin/` (Django admin nativo) e delega todo o resto para `website/urls.py`.

| Prefixo | Módulo | Descrição |
|---|---|---|
| `/` | `home.py` | Site público: home, testemunhos, contato |
| `/visitor/` | `visitor.py` | Registro público de visitantes |
| `/prayer-request/` | `prayer_request.py` | Pedidos de oração públicos |
| `/evangelism/` | `evangelism.py` | Registro de evangelizados |
| `/member/register/` | `member.py` | Cadastro de novos membros |
| `/new_converts/list/` | `member.py` | Lista de novos convertidos |
| `/event/` | `event.py` | Lista e detalhe de eventos públicos |
| `/translator/` | `translator.py` | Tradução simultânea em tempo real |
| `/login/`, `/logout/` | `member_auth.py` | Login / logout unificado |
| `/dashboard/` | `member_auth.py` | Dashboard do membro |
| `/profile/` | `member_auth.py` | Perfil do membro |
| `/roteiro-culto/` | `member_auth.py` | Roteiro do culto (membro) |
| `/words/` | `word_of_knowledge.py` | Palavras de conhecimento |
| `/consolidation/` | `member.py` | Acompanhamento de consolidação |
| `/register-visitor/` | `visitor.py` | Registro de visitante pelo membro |
| `/schedule/<id>/` | `member_schedule.py` | Detalhe de escala para membros |
| `/ministration/` | `ministration_*.py` | Área de ministração (admin) |
| `/ministry/` | `ministry.py` | CRUD de ministérios |
| `/attendance-course/` | `course_attendance.py` | Gestão de cursos + exportação PDF |
| `/teams/`, `/schedules/` | `schedules/` | Equipes e escalas mensais |
| `/admin-panel/` | `admin_panel/` | Painel administrativo completo |
| `/password-reset/` | Django auth | Redefinição de senha por e-mail |
| `/users/` | `user_management.py` | Gestão de usuários (admin) |

---

## Autenticação e Permissões

- Modelo `User` customizado com `email` como `USERNAME_FIELD`
- `user_type` controla o acesso: `member`, `admin`, `visitors`, `consolidation`, `events`
- Helpers `has_admin_access()` e `has_module_permission(module)` no modelo
- `LOGIN_URL = '/login/'`; após o login, redireciona para painel admin ou dashboard do membro conforme `user_type`
- Redefinição de senha completa via e-mail (fluxo nativo do Django)
- Mixins de permissão customizados nas views (`website/views/mixins.py`)

---

## WebSockets e Tradução em Tempo Real

Dois consumers em `website/consumers.py`:

| Consumer | Rota | Finalidade |
|---|---|---|
| `AudioConsumer` | `ws/audio/` | Recebe chunks de áudio PCM, envia ao Azure Speech SDK (PT-BR), transmite transcrições parciais e finais |
| `TranscriptConsumer` | `ws/transcript/` | Exibe as transcrições em tempo real para espectadores |

- **Channel layer**: Redis (`channels_redis`) quando `REDIS_URL` está definido; fallback para `InMemoryChannelLayer` em desenvolvimento
- **Entrada ASGI** (`connect/asgi.py`): `ProtocolTypeRouter` → HTTP → Django ASGI app; WebSocket → `AuthMiddlewareStack` → `URLRouter`

---

## Arquivos Estáticos e Mídia

- **Estáticos**: WhiteNoise com `CompressedManifestStaticFilesStorage` (arquivos versionados comprimidos com brotli em `staticfiles/`)
- Fontes customizadas: GigaSans, Integral, TuskerGrotesk
- PWA: manifesto (`manifest.json`) + service worker (`sw.js`)
- **Mídia**: filesystem local por padrão (`media/avatars/`, `event_banners/`, `profile_pictures/`); muda para **AWS S3** quando `USE_S3=TRUE`
- **Mídia Externa**: usa um storage dedicado; no S3 os objetos ficam em `private_media/` com URLs assinadas e downloads autorizados pelo Django

---

## Variáveis de Ambiente

| Variável | Finalidade |
|---|---|
| `SECRET_KEY` | Chave secreta do Django |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | Conexão PostgreSQL |
| `REDIS_URL` | Channel layer Redis (opcional; fallback in-memory) |
| `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | Redis usado pela fila de Mídia Externa |
| `DOMAIN` | Host permitido no deploy |
| `HOSTNAME`, `PORT` | Binding do servidor Docker (dev) |
| `USE_S3` | `TRUE` para usar S3 como storage de mídia |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_REGION_NAME` | Credenciais AWS S3 |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_KEY_VAULT_NAME` | Autenticação Azure Key Vault |
| `OPENAI_API_KEY` | Chave da OpenAI usada somente no backend |
| `OPENAI_TRANSCRIPTION_MODEL` | Modelo de transcrição (padrão: `whisper-1`) |
| `EXTERNAL_MEDIA_MAX_UPLOAD_MB` | Limite de upload de vídeo (padrão: 10240 MB) |
| `EXTERNAL_MEDIA_AUDIO_CHUNK_SECONDS` | Duração dos blocos de áudio enviados ao Whisper (padrão: 1200 s) |
| `EXTERNAL_MEDIA_TRANSLATION_BATCH_SIZE` | Quantidade de legendas traduzidas por chamada (padrão: 40) |
| `EXTERNAL_MEDIA_FFMPEG_TIMEOUT` | Limite por processo FFmpeg em segundos |
| `EXTERNAL_MEDIA_FFMPEG_PRESET` | Velocidade do encode H.264 (padrão: `veryfast`) |
| `EXTERNAL_MEDIA_AUTO_REFRAME_INTERVAL_FRAMES` | Intervalo entre análises de pessoa/rosto (padrão: 10 frames) |
| `EXTERNAL_MEDIA_AUTO_REFRAME_MAX_ANALYSIS_WIDTH` | Largura máxima usada pelo detector para reduzir CPU (padrão: 640 px) |
| `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`, `SITE_DOMAIN` | E-mail / redefinição de senha |

---

## Serviços Externos

| Serviço | Uso |
|---|---|
| **Azure Cognitive Services Speech** | Reconhecimento de fala PT-BR em tempo real no tradutor |
| **Azure Key Vault** (`kvfiladelfia`) | Armazena a chave da API de fala; buscada na inicialização via `ClientSecretCredential` |
| **Google Translate** (googletrans) | Traduz o texto reconhecido de PT-BR para Inglês e Holandês |
| **OpenAI** | Geração de texto via Responses API e transcrição de arquivos de áudio |
| **AWS S3** | Armazenamento opcional de arquivos de mídia |
| **SMTP (Gmail)** | E-mails de redefinição de senha |

---

## Como Rodar Localmente

### Pré-requisitos

- Docker e Docker Compose instalados

### Com Docker Compose

```bash
# Clone o repositório
git clone <url-do-repositorio>
cd connect

# Crie um arquivo .env com as variáveis necessárias
cp .env.example .env   # ajuste os valores

# Suba PostgreSQL, Redis, Django e o worker de mídia
docker compose up --build
```

O `docker-compose.yml` executa automaticamente `collectstatic`, `migrate` e inicia o servidor de desenvolvimento.

A aplicação ficará disponível em `http://localhost:8000`.

### Usando a camada compartilhada de IA

Views, consumers e tarefas podem acessar a mesma interface sem criar clientes da
OpenAI diretamente:

```python
from website.ai import get_ai_service

ai = get_ai_service()

resposta = ai.generate_text(
    "Crie um resumo desta solicitação.",
    model="gpt-4.1-mini",
    instructions="Responda em português do Brasil.",
)

transcricao = ai.transcribe(
    request.FILES["audio"],
    language="pt",
    prompt="Culto cristão em português do Brasil.",
)
```

A chave nunca deve ser enviada ao navegador; somente o backend deve usar esse
serviço. Para texto, cada fluxo escolhe o `model` explicitamente; apenas a
transcrição fica centralizada na `env`.

O módulo de Mídia Externa escolhe `gpt-4.1-mini` no próprio fluxo de tradução,
enquanto o Whisper permanece configurável por `OPENAI_TRANSCRIPTION_MODEL`.
Na renderização, os presets padrão usam H.264 com `CRF 23`, `preset veryfast`
e áudio copiado quando possível. Vídeos HDR, comuns em iPhone, são convertidos
explicitamente para SDR/Rec.709 para evitar cores lavadas após queimar a legenda.

### Sem Docker (ambiente virtual)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure as variáveis de ambiente e então:
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py runserver
```

---

## Deploy

O projeto está configurado para deploy no **Railway** (nixpacks) ou **Heroku/Dokku** (Aptfile).

| Arquivo | Finalidade |
|---|---|
| `Procfile` | `daphne -b 0.0.0.0 -p 8000 connect.asgi:application` |
| `nixpacks.toml` | Instala libs de sistema para renderização PDF (pango, cairo, gobject, etc.) |
| `Aptfile` | Mesmas libs para buildpacks Heroku/Dokku |
| `Dockerfile` | Base Python 3.10.5; produção usa `gunicorn`; volume de mídia em `/usr/src/platform/media` |

---

## Comandos de Gerenciamento

| Comando | Finalidade |
|---|---|
| `create_default_templates` | Cria templates padrão de consolidação (4 e 8 semanas) |
| `create_recurring_events` | Gera instâncias de eventos recorrentes |
| `ensure_superuser_members` | Cria registros `Member` para superusuários sem um; define `user_type = admin` |
| `migrate_legacy_ministries` | Migra estrutura antiga de ministérios para o novo schema |

```bash
python manage.py <nome_do_comando>
```

---

## Migrações

O projeto conta com **40 migrações** (`0001` a `0040`) cobrindo toda a evolução do schema: soft-delete e histórico em todos os modelos, eventos recorrentes, cursos de frequência, divisões de escala, roteiro do culto, aba de cantina, etc.

```bash
python manage.py migrate
```

---

## Painel Administrativo

O painel customizado (`/admin-panel/`) é separado do `/admin/` nativo do Django e oferece:

- **Dashboard** com estatísticas gerais
- **Membros**: listagem, detalhe, edição, status de consolidação
- **Visitantes**: listagem, detalhe, conversão
- **Eventos**: criação, edição, exclusão e eventos recorrentes
- **Acompanhamento (Follow-up)**: templates semanais, relatórios, progresso por convertido
- **Aba de cantina**: listagem, edição, marcação de pagamento
- **Ministérios**: CRUD, atribuição de membros e líderes
- **Ministração**: palavras de conhecimento (lista/aprovação), curas, gestão de membros
- **Escalas**: equipes, escalas mensais, dias/turnos, verificação de conflitos, impressão
- **Cursos de frequência**: CRUD completo, presença por aula, relatórios PDF
- **Testemunhos**: listagem, aprovação/rejeição, exclusão
- **Roteiro**: anúncios, reordenação, impressão
- **Relatórios**
- **Usuários**: listagem, reset de senhas
- **Bairros**: criação e edição hierárquica
- Controle de acesso por `user_type`: cada tipo de usuário vê apenas os módulos permitidos
