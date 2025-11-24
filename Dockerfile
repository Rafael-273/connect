FROM python:3.10.5

EXPOSE 8000
WORKDIR /usr/src/platform

# Instalar dependências do sistema para WeasyPrint
RUN apt-get update && apt-get install -y \
    libgobject-2.0-0 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /usr/src/platform/
    
RUN pip install -r requirements.txt

# Criar diretório de mídia e garantir permissões
RUN mkdir -p /usr/src/platform/media && \
    chmod -R 755 /usr/src/platform/media

# No need to copy files here since we're using volumes in docker-compose

CMD gunicorn --bind 0.0.0.0:8000 --reload connect.wsgi:application
