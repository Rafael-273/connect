FROM python:3.10.5

EXPOSE 8000
WORKDIR /connect

ADD requirements.txt /connect/

RUN pip install -r requirements.txt

ADD ./platform /connect

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "3", "--reload", "connect.wsgi:application"]