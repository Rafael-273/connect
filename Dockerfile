FROM python:3.10.5

EXPOSE 8000
WORKDIR /usr/src/platform

ADD requirements.txt /usr/src/platform/

RUN pip install -r requirements.txt

RUN pip install daphne

ADD ./platform /usr/src/platform

CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "connect.asgi:application"]
