"""Read-only expansion of central agenda occurrences; no Event copies are created."""
import calendar
import datetime

from website.models import Event

COLORS = ('#2563EB', '#7C3AED', '#08916C', '#D97706', '#DB2777', '#087E8B', '#9333EA', '#B45309')
MONTHS = ('', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro')


def team_color(team_id):
    return COLORS[(team_id - 1) % len(COLORS)]


def agenda_occurrences(period):
    start = period.replace(day=1)
    end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
    rows, warnings = [], []
    events = Event.objects.prefetch_related('dates').order_by('title', 'pk')
    for event in events:
        dates = []
        if event.is_recurring:
            anchor = event.event_date
            if event.recurrence_pattern in ('weekly', 'biweekly'):
                interval = 7 if event.recurrence_pattern == 'weekly' else 14
                offset = (start - anchor).days
                current = anchor + datetime.timedelta(days=((offset + interval - 1) // interval) * interval)
                while current <= end:
                    dates.append(current)
                    current += datetime.timedelta(days=interval)
            elif event.recurrence_pattern == 'monthly':
                if anchor.day <= end.day:
                    current = start.replace(day=anchor.day)
                    dates.append(current)
            else:
                warnings.append(f'{event.title}: recorrência personalizada precisa de datas explícitas neste mês.')
        elif start <= event.event_date <= end:
            dates.append(event.event_date)
        for date in dates:
            if not event.event_time:
                warnings.append(f'{event.title}: cadastre o horário na agenda para incluir este culto.')
                break
            rows.append(_row(event, date, event.event_time))
        for extra in event.dates.all():
            if start <= extra.event_date <= end:
                if extra.event_time:
                    rows.append(_row(event, extra.event_date, extra.event_time, extra))
                else:
                    warnings.append(f'{event.title}, {extra.event_date:%d/%m}: data adicional sem horário.')
    unique = {}
    for row in rows:
        unique.setdefault((row['event_id'], row['date'], row['time']), row)
    return sorted(unique.values(), key=lambda item: (item['date'], item['time'], item['key'])), list(dict.fromkeys(warnings))


def _row(event, date, time, extra=None):
    source = f'date:{extra.pk}' if extra else f'event:{event.pk}'
    return {
        'key': f'{source}@{date.isoformat()}',
        'event_id': event.pk, 'event_date_id': extra.pk if extra else None,
        'date': date.isoformat(), 'time': time.strftime('%H:%M'), 'name': event.title,
        'recurring': event.is_recurring and extra is None, 'recurring_event': event.is_recurring,
        'label': f'{event.title} · {date:%d/%m/%Y} · {time:%H:%M}',
    }
