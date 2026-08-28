FROM python:3.10.5 AS base

EXPOSE 8000
WORKDIR /usr/src/platform

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fontconfig && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /usr/src/platform/
    
RUN pip install -r requirements.txt

# Criar diretório de mídia e garantir permissões
RUN mkdir -p /usr/src/platform/media && \
    chmod -R 755 /usr/src/platform/media

FROM base AS web

CMD gunicorn --bind 0.0.0.0:8000 --reload connect.wsgi:application

FROM base AS media-worker

COPY requirements-diarization.txt /usr/src/platform/

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 600 --retries 10 -r requirements-diarization.txt
