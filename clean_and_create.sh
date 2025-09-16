#!/bin/bash

# Create a script to clean the database
cat > clean_events.py << 'EOF'
from django.db import connection

# Run SQL directly to forcefully remove events with these slugs
event_titles = [
    'culto-de-domingo',
    'culto-de-jovens',
    'reuniao-de-oracao',
    'estudo-biblico',
    'grupo-de-mulheres',
    'ensaio-do-coral',
    'grupo-de-intercessao-matinal'
]

with connection.cursor() as cursor:
    for slug in event_titles:
        print(f"Removing events with slug: {slug}")
        cursor.execute("DELETE FROM website_event WHERE slug = %s", [slug])
        rows_deleted = cursor.rowcount
        print(f"  -> {rows_deleted} row(s) deleted")

print("Database cleanup completed.")
EOF

# Run the cleanup script
docker-compose exec -T web-project bash -c "cat > clean_events.py" < clean_events.py
docker-compose exec web-project python manage.py shell -c "exec(open('clean_events.py').read())"

# Now run the original script
./create_test_events.sh
