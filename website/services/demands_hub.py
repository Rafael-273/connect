"""Serviço de dados para a tela Demandas e Eventos."""

import datetime
import json
from collections import OrderedDict, defaultdict

from django.db.models import Count, Prefetch, Q
from django.utils import timezone

from website.models.event import Event
from website.models.media_content import CONTENT_TYPE_CHOICES, MediaContent
from website.models.media_event_type import MediaEventType
from website.models.media_organization import MediaSubTeam
from website.models.media_task import MediaTask
from website.models.user import User

CONTENT_TYPE_META = {
    'artwork': {'icon': 'palette', 'emoji': '🎨', 'group': 'design'},
    'reel': {'icon': 'film', 'emoji': '🎥', 'group': 'video'},
    'carousel': {'icon': 'images', 'emoji': '🎨', 'group': 'design'},
    'story': {'icon': 'mobile-alt', 'emoji': '📱', 'group': 'social'},
    'photography': {'icon': 'camera', 'emoji': '📷', 'group': 'photo'},
    'video': {'icon': 'video', 'emoji': '🎥', 'group': 'video'},
    'livestream': {'icon': 'broadcast-tower', 'emoji': '📡', 'group': 'video'},
    'youtube': {'icon': 'youtube', 'emoji': '▶️', 'group': 'video'},
    'whatsapp': {'icon': 'comment', 'emoji': '💬', 'group': 'social'},
    'website_banner': {'icon': 'desktop', 'emoji': '🖥️', 'group': 'design'},
    'devotional': {'icon': 'book-open', 'emoji': '📖', 'group': 'social'},
    'announcement': {'icon': 'bullhorn', 'emoji': '📢', 'group': 'social'},
    'coordination': {'icon': 'clipboard-list', 'emoji': '📋', 'group': 'org'},
    'contact': {'icon': 'phone', 'emoji': '🤝', 'group': 'org'},
    'approval': {'icon': 'check-circle', 'emoji': '✅', 'group': 'org'},
}

# Tipos para criação rápida — produção técnica + organização
DEMAND_QUICK_TYPES = [
    # Produção de conteúdo
    {
        'value': 'artwork', 'label': 'Design', 'emoji': '🎨', 'group': 'technical',
        'hint': 'Artes, banners e posts',
        'due_label': 'Prazo de produção', 'pub_label': 'Publicação',
        'default_roles': ['Designer', 'Revisor', 'Publicador'],
    },
    {
        'value': 'video', 'label': 'Edição de vídeo', 'emoji': '🎥', 'group': 'technical',
        'hint': 'Vídeos, cortes e finalização',
        'due_label': 'Prazo de edição', 'pub_label': 'Publicação',
        'default_roles': ['Filmmaker', 'Editor', 'Publicador'],
    },
    {
        'value': 'reel', 'label': 'Reel', 'emoji': '📱', 'group': 'technical',
        'hint': 'Reels e vídeos curtos',
        'due_label': 'Prazo de gravação/edição', 'pub_label': 'Publicação',
        'default_roles': ['Filmmaker', 'Editor', 'Publicador'],
    },
    {
        'value': 'photography', 'label': 'Fotografia', 'emoji': '📷', 'group': 'technical',
        'hint': 'Cobertura e registros',
        'due_label': 'Cobertura até', 'pub_label': 'Entrega das fotos',
        'default_roles': ['Fotógrafo', 'Editor', 'Publicador'],
    },
    {
        'value': 'story', 'label': 'Stories', 'emoji': '💬', 'group': 'technical',
        'hint': 'Stories e conteúdo efêmero',
        'due_label': 'Produção até', 'pub_label': 'Publicação',
    },
    {
        'value': 'carousel', 'label': 'Carrossel', 'emoji': '🖼️', 'group': 'technical',
        'hint': 'Posts em sequência',
        'due_label': 'Prazo de produção', 'pub_label': 'Publicação',
    },
    {
        'value': 'livestream', 'label': 'Transmissão', 'emoji': '📡', 'group': 'technical',
        'hint': 'Live e transmissões',
        'due_label': 'Preparação técnica', 'pub_label': 'Data da transmissão',
        'default_roles': ['Operador', 'Editor', 'Publicador'],
    },
    {
        'value': 'announcement', 'label': 'Comunicado', 'emoji': '📢', 'group': 'technical',
        'hint': 'Avisos e comunicados',
        'due_label': 'Redação até', 'pub_label': 'Divulgação',
    },
    {
        'value': 'devotional', 'label': 'Devocional', 'emoji': '📖', 'group': 'technical',
        'hint': 'Conteúdo devocional',
        'due_label': 'Gravação/redação até', 'pub_label': 'Publicação',
    },
    # Organização e alinhamento
    {
        'value': 'coordination', 'label': 'Organização', 'emoji': '📋', 'group': 'organizational',
        'hint': 'Montar estrutura, reservas, logística',
        'due_label': 'Organizar até', 'pub_label': 'Tudo pronto em',
    },
    {
        'value': 'contact', 'label': 'Contato', 'emoji': '🤝', 'group': 'organizational',
        'hint': 'Falar com alguém, alinhar combinados',
        'due_label': 'Contato até', 'pub_label': 'Confirmação / retorno',
    },
    {
        'value': 'approval', 'label': 'Aprovação', 'emoji': '✅', 'group': 'organizational',
        'hint': 'Validar material ou decisão',
        'due_label': 'Enviar para aprovação', 'pub_label': 'Aprovação até',
    },
]

ASSIGNMENT_ROLE_PRESETS = [
    'Filmmaker',
    'Editor',
    'Publicador',
    'Designer',
    'Fotógrafo',
    'Revisor',
    'Operador',
    'Responsável',
]

DEMAND_QUICK_TYPE_CHOICES = [(t['value'], t['label']) for t in DEMAND_QUICK_TYPES]

DEMAND_TYPES_TECHNICAL = [t for t in DEMAND_QUICK_TYPES if t['group'] == 'technical']
DEMAND_TYPES_ORGANIZATIONAL = [t for t in DEMAND_QUICK_TYPES if t['group'] == 'organizational']

for _t in DEMAND_QUICK_TYPES:
    _meta = CONTENT_TYPE_META.get(_t['value'], {})
    _t.setdefault('icon', _meta.get('icon', 'file'))
    _t.setdefault('default_roles', ['Responsável'])


def get_demand_type_meta(content_type):
    for t in DEMAND_QUICK_TYPES:
        if t['value'] == content_type:
            return t
    return None


def get_demand_type_roles_map():
    return {t['value']: t.get('default_roles', ['Responsável']) for t in DEMAND_QUICK_TYPES}


def _parse_assignment_date(value):
    if not value:
        return None
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        naive = datetime.datetime.combine(value, datetime.time(hour=12, minute=0))
        return timezone.make_aware(naive) if timezone.is_naive(naive) else naive
    if isinstance(value, str):
        try:
            parts = value.split('-')
            d = datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            naive = datetime.datetime.combine(d, datetime.time(hour=12, minute=0))
            return timezone.make_aware(naive) if timezone.is_naive(naive) else naive
        except (ValueError, IndexError):
            return None
    return value


def parse_demand_assignments(post_data, prefix='demand'):
    """Extrai etapas (papel + responsável + prazo) do POST."""
    roles = post_data.getlist(f'{prefix}-assignment_role')
    users = post_data.getlist(f'{prefix}-assignment_user')
    dues = post_data.getlist(f'{prefix}-assignment_due')
    descs = post_data.getlist(f'{prefix}-assignment_desc')
    ids = post_data.getlist(f'{prefix}-assignment_id')
    assignments = []
    for i, role in enumerate(roles):
        role = (role or '').strip()
        user = users[i].strip() if i < len(users) and users[i] else ''
        due = dues[i].strip() if i < len(dues) and dues[i] else ''
        desc = descs[i].strip() if i < len(descs) and descs[i] else ''
        task_id = ids[i].strip() if i < len(ids) and ids[i] else ''
        if not role and not user and not due and not desc:
            continue
        assignments.append({
            'role': role or 'Etapa',
            'user_id': int(user) if user.isdigit() else None,
            'due_date': due or None,
            'description': desc,
            'task_id': int(task_id) if task_id.isdigit() else None,
        })
    return assignments


def sync_content_assignments(content, assignments):
    """Sincroniza MediaTask com as etapas informadas."""
    keep_ids = {a['task_id'] for a in assignments if a.get('task_id')}
    if keep_ids:
        content.tasks.exclude(pk__in=keep_ids).delete()
    else:
        content.tasks.all().delete()

    for order, data in enumerate(assignments):
        due_dt = _parse_assignment_date(data.get('due_date'))
        task_id = data.get('task_id')
        if task_id:
            task = MediaTask.objects.filter(pk=task_id, content=content).first()
            if not task:
                continue
            task.title = data['role']
            task.assigned_to_id = data.get('user_id')
            task.due_date = due_dt
            task.description = data.get('description', '')
            task.save(update_fields=['title', 'assigned_to_id', 'due_date', 'description', 'update_at'])
        else:
            MediaTask.objects.create(
                content=content,
                title=data['role'],
                assigned_to_id=data.get('user_id'),
                due_date=due_dt,
                description=data.get('description', ''),
            )

    tasks = list(content.tasks.select_related('assigned_to__member').order_by('due_date', 'pk'))
    first_assigned = next((t for t in tasks if t.assigned_to_id), None)
    earliest = next((t.due_date for t in tasks if t.due_date), None)
    content.responsible_id = first_assigned.assigned_to_id if first_assigned else None
    content.due_date = earliest
    content.save(update_fields=['responsible_id', 'due_date', 'update_at'])


def build_assignment_rows(content):
    """Linhas iniciais para o formulário de etapas."""
    tasks = list(content.tasks.order_by('due_date', 'pk'))
    if tasks:
        return [
            {
                'id': t.pk,
                'role': t.title,
                'user_id': t.assigned_to_id or '',
                'due_date': t.due_date.date().isoformat() if t.due_date else '',
                'description': t.description or '',
                'is_custom_role': t.title not in ASSIGNMENT_ROLE_PRESETS,
            }
            for t in tasks
        ]
    if content.responsible_id or content.due_date:
        return [{
            'id': '',
            'role': 'Responsável',
            'user_id': content.responsible_id or '',
            'due_date': content.due_date.date().isoformat() if content.due_date else '',
            'description': '',
            'is_custom_role': False,
        }]
    return [{'id': '', 'role': '', 'user_id': '', 'due_date': '', 'description': '', 'is_custom_role': False}]


def _assignment_summary(content):
    names = []
    for t in content.tasks.all():
        name = _person_name(t.assigned_to)
        if name and name not in names:
            names.append(name)
    if names:
        if len(names) == 1:
            return names[0]
        return f'{names[0]} +{len(names) - 1}'
    return _person_name(content.responsible)

PHASE_LABELS = {
    'before': 'Antes do evento',
    'during': 'Durante o evento',
    'after': 'Depois do evento',
    'unscheduled': 'Sem prazo definido',
}

MONTHS_SHORT_PT = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']


def _date_label_short(d):
    return f'{d.day} {MONTHS_SHORT_PT[d.month - 1]}'


MONTH_NAMES_PT = [
    '', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
    'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro',
]

DONE_STATUSES = {'approved', 'scheduled', 'published'}


def content_type_meta(content_type):
    return CONTENT_TYPE_META.get(content_type, {'icon': 'file', 'emoji': '📄', 'group': 'other'})


def _ref_date(content):
    if content.publication_date:
        return content.publication_date.date()
    if content.due_date:
        return content.due_date.date()
    return None


def timeline_phase(content, event_date):
    ref = _ref_date(content)
    if not ref or not event_date:
        return 'unscheduled'
    if ref < event_date:
        return 'before'
    if ref == event_date:
        return 'during'
    return 'after'


def group_contents_by_phase(contents, event_date):
    groups = OrderedDict((k, []) for k in ('before', 'during', 'after', 'unscheduled'))
    for content in contents:
        phase = timeline_phase(content, event_date)
        groups[phase].append(content)
    return [(PHASE_LABELS[k], groups[k]) for k in groups if groups[k]]


def _month_key(d):
    return (d.year, d.month)


def _month_label(key):
    y, m = key
    return f'{MONTH_NAMES_PT[m].upper()} {y}' if y != timezone.now().year else MONTH_NAMES_PT[m].upper()


def build_sidebar_items(request):
    """Retorna lista de meses com eventos e conteúdos livres."""
    params = request.GET
    kind = params.get('kind', 'all')
    q = params.get('q', '').strip()
    responsible_id = params.get('responsible', '')
    team_id = params.get('team', '')
    event_type_id = params.get('event_type', '')
    period_from = params.get('period_from', '')
    period_to = params.get('period_to', '')

    today = timezone.now().date()

    events_qs = Event.objects.select_related('event_type').annotate(
        content_count=Count('media_contents'),
        done_count=Count('media_contents', filter=Q(media_contents__status__in=DONE_STATUSES)),
    )

    if q:
        events_qs = events_qs.filter(Q(title__icontains=q) | Q(location__icontains=q))
    if event_type_id:
        events_qs = events_qs.filter(event_type_id=event_type_id)
    if period_from:
        events_qs = events_qs.filter(event_date__gte=period_from)
    if period_to:
        events_qs = events_qs.filter(event_date__lte=period_to)
    if not period_from and not period_to:
        events_qs = events_qs.filter(event_date__gte=today - datetime.timedelta(days=30))

    if responsible_id or team_id:
        content_filter = Q()
        if responsible_id:
            content_filter &= Q(media_contents__responsible_id=responsible_id)
        if team_id:
            content_filter &= Q(media_contents__sub_team_id=team_id)
        events_qs = events_qs.filter(content_filter).distinct()

    events_qs = events_qs.order_by('event_date')

    free_qs = MediaContent.objects.filter(event__isnull=True).select_related(
        'responsible__member', 'sub_team'
    ).prefetch_related(
        Prefetch('tasks', queryset=MediaTask.objects.select_related('assigned_to__member'))
    )
    if q:
        free_qs = free_qs.filter(Q(title__icontains=q) | Q(description__icontains=q))
    if responsible_id:
        free_qs = free_qs.filter(responsible_id=responsible_id)
    if team_id:
        free_qs = free_qs.filter(sub_team_id=team_id)
    if period_from:
        free_qs = free_qs.filter(
            Q(publication_date__date__gte=period_from) | Q(due_date__date__gte=period_from)
        )
    if period_to:
        free_qs = free_qs.filter(
            Q(publication_date__date__lte=period_to) | Q(due_date__date__lte=period_to)
        )
    free_qs = free_qs.order_by('publication_date', 'due_date', '-created_at')

    months = OrderedDict()

    if kind in ('all', 'events'):
        for event in events_qs:
            key = _month_key(event.event_date)
            if key not in months:
                months[key] = {'label': _month_label(key), 'items': []}
            months[key]['items'].append({
                'type': 'event',
                'pk': event.pk,
                'key': f'event-{event.pk}',
                'title': event.title,
                'date': event.event_date,
                'date_label': _date_label_short(event.event_date),
                'content_count': event.content_count,
                'done_count': event.done_count,
                'event_type': event.event_type.name if event.event_type_id else None,
                'icon': 'calendar-alt',
            })

    if kind in ('all', 'free'):
        for content in free_qs:
            ref = _ref_date(content) or today
            key = _month_key(ref)
            meta = content_type_meta(content.content_type)
            if key not in months:
                months[key] = {'label': _month_label(key), 'items': []}
            months[key]['items'].append({
                'type': 'free',
                'pk': content.pk,
                'key': f'free-{content.pk}',
                'title': content.title,
                'date': ref,
                'date_label': _date_label_short(ref),
                'content_type': content.get_content_type_display(),
                'icon': meta['icon'],
                'status': content.status,
                'responsible': _assignment_summary(content),
            })

    result = []
    for key in sorted(months.keys()):
        month = months[key]
        month['items'].sort(key=lambda x: x['date'])
        result.append(month)
    return result


def _person_name(user):
    if not user:
        return None
    if hasattr(user, 'member') and user.member:
        return user.member.name
    return user.email


def build_event_detail(event_pk):
    event = Event.objects.select_related('event_type').get(pk=event_pk)
    contents = list(
        MediaContent.objects.filter(event=event)
        .select_related('responsible__member', 'sub_team', 'assigned_role')
        .prefetch_related(
            Prefetch('tasks', queryset=MediaTask.objects.select_related('assigned_to__member').order_by('due_date'))
        )
        .order_by('due_date', 'publication_date', 'title')
    )
    total = len(contents)
    done = sum(1 for c in contents if c.status in DONE_STATUSES)
    phases = group_contents_by_phase(contents, event.event_date)

    for _, phase_contents in phases:
        for content in phase_contents:
            content.type_meta = content_type_meta(content.content_type)
            content.responsible_name = _person_name(content.responsible)
            content.assignment_rows = build_assignment_rows(content)
            content.assignments_json = json.dumps(content.assignment_rows)

    return {
        'event': event,
        'contents': contents,
        'phases': phases,
        'total': total,
        'done': done,
        'progress': int(done / total * 100) if total else 0,
    }


def build_free_detail(content_pk):
    content = MediaContent.objects.select_related(
        'responsible__member', 'sub_team', 'assigned_role'
    ).prefetch_related(
        Prefetch('tasks', queryset=MediaTask.objects.select_related('assigned_to__member').order_by('due_date'))
    ).get(pk=content_pk, event__isnull=True)
    content.type_meta = content_type_meta(content.content_type)
    content.responsible_name = _person_name(content.responsible)
    content.assignment_rows = build_assignment_rows(content)
    content.assignments_json = json.dumps(content.assignment_rows)
    return {'content': content}


def parse_selected(selected):
    if not selected:
        return None, None
    if selected.startswith('event-'):
        try:
            return 'event', int(selected.replace('event-', ''))
        except ValueError:
            return None, None
    if selected.startswith('free-'):
        try:
            return 'free', int(selected.replace('free-', ''))
        except ValueError:
            return None, None
    return None, None


def get_responsible_picker_options():
    """Membros da mídia disponíveis como responsáveis."""
    from website.models.ministry_membership import MinistryMembership

    user_ids = (
        MinistryMembership.objects.filter(
            ministry__name__iexact='Mídia Externa',
            is_active=True,
            member__user__isnull=False,
            member__is_active=True,
        )
        .values_list('member__user_id', flat=True)
    )
    qs = (
        User.objects.filter(pk__in=user_ids)
        .select_related('member')
        .order_by('member__name')
    )
    if not qs.exists():
        qs = (
            User.objects.filter(member__isnull=False, member__is_active=True)
            .select_related('member')
            .order_by('member__name')
        )
    return qs


def get_filter_context():
    return {
        'users': User.objects.filter(member__isnull=False).select_related('member').order_by('member__name'),
        'teams': MediaSubTeam.objects.filter(is_active=True).order_by('name'),
        'event_types': MediaEventType.objects.filter(is_active=True).order_by('sort_order', 'name'),
        'content_types': CONTENT_TYPE_CHOICES,
    }


def get_templates():
    return (
        MediaEventType.objects.select_related('planning_template')
        .annotate(item_count=Count('planning_template__items'))
        .order_by('sort_order', 'name')
    )


HUB_FILTER_KEYS = ('kind', 'q', 'responsible', 'team', 'event_type', 'period_from', 'period_to')


def build_hub_query(request, **extra):
    """Monta query string preservando filtros do hub."""
    from urllib.parse import urlencode

    params = {}
    for key in HUB_FILTER_KEYS:
        val = extra.get(key, request.GET.get(key, request.POST.get(key, '')))
        if val:
            params[key] = val
    for key, val in extra.items():
        if key not in HUB_FILTER_KEYS and val:
            params[key] = val
    qs = urlencode(params)
    return f'?{qs}' if qs else ''


def hub_redirect_url(request, **extra):
    from django.urls import reverse
    return f"{reverse('media_content_list')}{build_hub_query(request, **extra)}"


def content_hub_selected(content):
    if getattr(content, 'event_id', None):
        return f'event-{content.event_id}'
    return f'free-{content.pk}'


def content_hub_url(content):
    from django.urls import reverse
    return f"{reverse('media_content_list')}?selected={content_hub_selected(content)}"
