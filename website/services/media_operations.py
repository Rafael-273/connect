from django.db.models import Q
from website.models import MediaContent


def filtered_demands(form):
    qs = MediaContent.objects.select_related('event', 'sub_team', 'responsible__member')
    if not form.is_valid():
        return qs.none()
    data = form.cleaned_data
    if data.get('q'):
        qs = qs.filter(Q(title__icontains=data['q']) | Q(description__icontains=data['q']))
    for field in ('event', 'status', 'priority'):
        if data.get(field):
            qs = qs.filter(**{field: data[field]})
    if data.get('team'):
        qs = qs.filter(sub_team=data['team'])
    if data.get('responsible'):
        qs = qs.filter(Q(responsible=data['responsible']) | Q(tasks__assigned_to=data['responsible'], tasks__deleted__isnull=True))
    if data.get('kind'):
        qs = qs.filter(event__isnull=data['kind'] == 'free')
    if data.get('due_from'):
        qs = qs.filter(due_date__date__gte=data['due_from'])
    if data.get('due_to'):
        qs = qs.filter(due_date__date__lte=data['due_to'])
    return qs.distinct()
