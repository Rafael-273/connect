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
