FROM python:3.10.5

EXPOSE 8000
WORKDIR /usr/src/platform

RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl1.1 \
    && rm -rf /var/lib/apt/lists/*

ADD requirements.txt /usr/src/platform/
RUN pip install -r requirements.txt
RUN pip install daphne

ADD ./platform /usr/src/platform

CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "connect.asgi:application"]
