# A versão fixa 3.10.5 usava Debian Bullseye, cujos pacotes de segurança já
# não estão mais disponíveis de forma consistente. Bookworm mantém o FFmpeg
# instalável também em imagens ARM64.
FROM python:3.10-slim-bookworm AS base

EXPOSE 8000
WORKDIR /usr/src/platform

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fontconfig && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

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
