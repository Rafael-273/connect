# A versão fixa 3.10.5 usava Debian Bullseye, cujos pacotes de segurança já
# não estão mais disponíveis de forma consistente. Bookworm mantém o FFmpeg
# instalável também em imagens ARM64.
FROM python:3.10-slim-bookworm AS base

# Unbuffered stdout/stderr is required so log lines reach the platform's log
# collector as they happen. Without this, Python block-buffers stdout when it
# isn't attached to a TTY (e.g. inside a container), so `logger.info(...)` /
# `print(...)` calls can sit in memory for hours and never appear in the
# Render Workflow log viewer if the process gets killed by a timeout before
# the buffer flushes.
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
WORKDIR /usr/src/platform

# Use HTTPS para evitar caches HTTP intermediários entregando pacotes alterados.
# Limpe índices/arquivos parciais e repita a transação completa em falhas transitórias.
# As validações de certificados, assinaturas e hashes permanecem habilitadas.
RUN set -eux; \
    find /etc/apt -type f \( -name '*.sources' -o -name '*.list' \) \
        -exec sed -i 's|http://deb.debian.org|https://deb.debian.org|g; s|http://security.debian.org|https://security.debian.org|g' {} +; \
    for attempt in 1 2 3; do \
        rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*; \
        if apt-get -o APT::Update::Error-Mode=any -o Acquire::https::No-Cache=true update && \
            apt-get -o Acquire::Retries=3 -o Acquire::https::No-Cache=true install -y --no-install-recommends ffmpeg fontconfig; then \
            break; \
        fi; \
        if [ "$attempt" = 3 ]; then exit 1; fi; \
        sleep 5; \
    done; \
    apt-get clean && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

COPY requirements.txt /usr/src/platform/
    
RUN python -m pip install --no-cache-dir --retries 10 --timeout 120 \
    --index-url https://pypi.org/simple -r requirements.txt

# Criar diretório de mídia e garantir permissões
RUN mkdir -p /usr/src/platform/media && \
    chmod -R 755 /usr/src/platform/media

FROM base AS web

CMD gunicorn --bind 0.0.0.0:8000 --reload --timeout 1800 --graceful-timeout 30 connect.wsgi:application

FROM base AS media-worker

COPY requirements-diarization.txt /usr/src/platform/

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --timeout 600 --retries 10 \
    --index-url https://pypi.org/simple -r requirements-diarization.txt

# Default target for Render Workflows. Docker Compose continues to select the
# explicit ``web`` and ``media-worker`` targets above for local development.
FROM media-worker AS render-workflow

# Render Workflows currently expose about 2 GB of ephemeral scratch. Keep a
# safety reserve while allowing the streaming estimate to pass the disk guard.
ENV MEDIA_ROOT=/tmp/media \
    EXTERNAL_MEDIA_WORKSPACE_ROOT=/tmp/media-jobs \
    EXTERNAL_MEDIA_WORKSPACE_MIN_FREE_GB=0.25

COPY . /usr/src/platform/
