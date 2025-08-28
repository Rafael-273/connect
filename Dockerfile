FROM python:3.10.5

EXPOSE 8000
WORKDIR /platform

ADD requirements.txt /platform
    
RUN pip install -r requirements.txt

ADD ./platform /platform

CMD gunicorn --bind 0.0.0.0:8000 --reload platform.wsgi:application
