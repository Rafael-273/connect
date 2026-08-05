web: daphne -b 0.0.0.0 -p 8000 connect.asgi:application
worker: celery -A connect worker --loglevel=info --concurrency=1
