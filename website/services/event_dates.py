"""Reconcile agenda dates without replacing references to unchanged occurrences."""
from django.db import transaction
from website.models import EventDate


@transaction.atomic
def sync_event_dates(event, rows):
    existing = list(EventDate.objects.select_for_update().filter(event=event))
    kept = set()
    for row in rows:
        date = EventDate._meta.get_field('event_date').to_python(row['event_date'])
        time = EventDate._meta.get_field('event_time').to_python(row.get('event_time'))
        match = next((item for item in existing if item.pk not in kept
                      and item.event_date == date and item.event_time == time), None)
        if match is None:
            match = EventDate.objects.create(event=event, event_date=date, event_time=time)
        kept.add(match.pk)
    for item in existing:
        if item.pk not in kept:
            item.delete()
