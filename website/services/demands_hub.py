"""Serviço de dados para a tela Demandas e Eventos."""

import datetime
import json
from collections import OrderedDict

from django.core.exceptions import ValidationError
from django.db import transaction
from website.services.ministry_organization import media_teams, media_ministry
from website.services.demand_assignments import validate_ministry_assignees, attach_assignment_warnings

from django.db.models import Count, Prefetch, Q
from django.utils import timezone

from website.models.event import Event
from website.models.media_content import CONTENT_TYPE_CHOICES, MediaContent, STATUS_CHOICES
from website.models.media_event_type import MediaEventType
from website.models.media_task import MediaTask
from website.models.user import User

_CONTENT_TYPE_LABELS = dict(CONTENT_TYPE_CHOICES)
_STATUS_LABELS = dict(STATUS_CHOICES)
CONTENT_STATUS_PROTECTED = frozenset({'scheduled', 'published', 'review'})

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
    # Produção de conteúdo (lista resumida; formatos legados continuam no model)
    {
        'value': 'artwork', 'label': 'Design', 'emoji': '🎨', 'group': 'technical',
        'hint': 'Posts, stories, carrosséis, banners e artes',
        'due_label': 'Prazo de produção', 'pub_label': 'Publicação',
        'default_roles': ['Designer', 'Revisor', 'Publicador'],
    },
    {
        'value': 'video', 'label': 'Vídeo', 'emoji': '🎥', 'group': 'technical',
        'hint': 'Vídeos, reels, cortes e devocionais',
        'due_label': 'Prazo de edição', 'pub_label': 'Publicação',
        'default_roles': ['Filmmaker', 'Editor', 'Publicador'],
    },
    {
        'value': 'photography', 'label': 'Fotografia', 'emoji': '📷', 'group': 'technical',
        'hint': 'Cobertura e registros',
        'due_label': 'Cobertura até', 'pub_label': 'Entrega das fotos',
        'default_roles': ['Fotógrafo', 'Editor', 'Publicador'],
    },
    {
        'value': 'livestream', 'label': 'Transmissão', 'emoji': '📡', 'group': 'technical',
        'hint': 'Lives e transmissões',
        'due_label': 'Preparação técnica', 'pub_label': 'Data da transmissão',
        'default_roles': ['Operador', 'Editor', 'Publicador'],
    },
    {
        'value': 'announcement', 'label': 'Comunicado', 'emoji': '📢', 'group': 'technical',
        'hint': 'Avisos, textos e comunicados',
        'due_label': 'Redação até', 'pub_label': 'Divulgação',
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

# Tipos granulares ainda existentes no banco → categoria resumida do hub
LEGACY_CONTENT_TYPE_ALIASES = {
    'carousel': 'artwork',
    'story': 'artwork',
    'website_banner': 'artwork',
    'reel': 'video',
    'youtube': 'video',
    'devotional': 'video',
    'whatsapp': 'announcement',
}


def quick_content_type(content_type):
    """Mapeia tipo legado para a categoria resumida do hub."""
    return LEGACY_CONTENT_TYPE_ALIASES.get(content_type, content_type)


def get_demand_type_meta(content_type):
    quick = quick_content_type(content_type)
    for t in DEMAND_QUICK_TYPES:
        if t['value'] == quick:
            return t
    return None


def get_demand_type_roles_map():
    roles = {t['value']: t.get('default_roles', ['Responsável']) for t in DEMAND_QUICK_TYPES}
    for legacy, quick in LEGACY_CONTENT_TYPE_ALIASES.items():
        roles.setdefault(legacy, roles.get(quick, ['Responsável']))
    return roles


def extra_demand_type_option(content_type):
    """Opção extra no select quando a demanda usa formato legado."""
    if not content_type:
        return None
    if any(t['value'] == content_type for t in DEMAND_QUICK_TYPES):
        return None
    if content_type not in _CONTENT_TYPE_LABELS:
        return None
    parent = get_demand_type_meta(content_type)
    meta = CONTENT_TYPE_META.get(content_type, {})
    roles = get_demand_type_roles_map().get(content_type, ['Responsável'])
    return {
        'value': content_type,
        'label': f'{_CONTENT_TYPE_LABELS[content_type]} (formato anterior)',
        'hint': parent.get('hint', '') if parent else '',
        'icon': meta.get('icon', 'file'),
        'group': parent.get('group', 'technical') if parent else 'technical',
        'default_roles': roles,
        'due_label': parent.get('due_label', 'Prazo') if parent else 'Prazo',
        'pub_label': parent.get('pub_label', 'Publicação') if parent else 'Publicação',
    }


def extra_demand_type_options_for(*content_types):
    seen = set()
    options = []
    for content_type in content_types:
        if not content_type or content_type in seen:
            continue
        seen.add(content_type)
        option = extra_demand_type_option(content_type)
        if option:
            options.append(option)
    return options


def compress_assignment_offset(days_raw, relation_raw):
    relation = (relation_raw or 'before').strip()
    if relation == 'on':
        return 0
    if days_raw in (None, ''):
        return None
    try:
        days = int(days_raw)
    except (TypeError, ValueError):
        return None
    if days < 0:
        return None
    if relation == 'before':
        return -days
    if relation == 'after':
        return days
    return None


def decompress_assignment_offset(offset):
    if offset is None:
        return {'due_days': '', 'due_relation': 'before'}
    if offset == 0:
        return {'due_days': '0', 'due_relation': 'on'}
    if offset < 0:
        return {'due_days': str(abs(offset)), 'due_relation': 'before'}
    return {'due_days': str(offset), 'due_relation': 'after'}


def _task_due_datetime(content, offset_days):
    if offset_days is None:
        return None
    event = getattr(content, 'event', None)
    if event and event.event_date:
        from website.services.media_planning import offset_to_datetime
        return offset_to_datetime(event.event_date, offset_days)
    return None


def _task_offset_for_form(task, content):
    if task.due_offset_days is not None:
        return decompress_assignment_offset(task.due_offset_days)
    if task.due_date and content.event_id and content.event and content.event.event_date:
        delta = (task.due_date.date() - content.event.event_date).days
        return decompress_assignment_offset(delta)
    return decompress_assignment_offset(None)


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
    due_days = post_data.getlist(f'{prefix}-assignment_due_days')
    due_relations = post_data.getlist(f'{prefix}-assignment_due_relation')
    descs = post_data.getlist(f'{prefix}-assignment_desc')
    ids = post_data.getlist(f'{prefix}-assignment_id')
    assignments = []
    for i, role in enumerate(roles):
        role = (role or '').strip()
        user = users[i].strip() if i < len(users) and users[i] else ''
        days_raw = due_days[i].strip() if i < len(due_days) and due_days[i] is not None else ''
        relation = due_relations[i].strip() if i < len(due_relations) and due_relations[i] else 'before'
        offset = compress_assignment_offset(days_raw, relation)
        desc = descs[i].strip() if i < len(descs) and descs[i] else ''
        task_id = ids[i].strip() if i < len(ids) and ids[i] else ''
        if not role and not user and offset is None and not desc:
            continue
        assignments.append({
            'role': role or 'Etapa',
            'user_id': int(user) if user.isascii() and user.isdigit() and len(user) <= 18 else None,
            'due_offset_days': offset,
            'description': desc,
            'task_id': int(task_id) if task_id.isascii() and task_id.isdigit() and len(task_id) <= 18 else None,
        })
    return assignments


def sync_content_status_from_tasks(content):
    """Atualiza o status da demanda conforme o progresso das etapas."""
    if content.status in CONTENT_STATUS_PROTECTED:
        return content.status

    tasks = list(content.tasks.order_by('sort_order', 'pk'))
    if not tasks:
        return content.status

    completed = sum(1 for task in tasks if task.status == 'completed')
    in_progress = sum(1 for task in tasks if task.status == 'in_progress')
    total = len(tasks)

    if completed == total:
        new_status = 'approved'
    elif completed > 0 or in_progress > 0:
        new_status = 'in_progress'
    else:
        new_status = 'pending'

    if content.status != new_status:
        MediaContent.objects.filter(pk=content.pk).update(
            status=new_status,
            update_at=timezone.now(),
        )
        content.status = new_status
    return content.status


def content_status_label(status):
    return _STATUS_LABELS.get(status, status)


@transaction.atomic
def sync_content_assignments(content, assignments):
    """Sincroniza MediaTask com as etapas informadas."""
    validate_ministry_assignees([a.get('user_id') for a in assignments])
    user_ids = {a['user_id'] for a in assignments if a.get('user_id')}
    if user_ids - set(User.objects.filter(pk__in=user_ids, is_active=True).values_list('pk', flat=True)):
        raise ValidationError('Responsável inválido ou inativo.')
    keep_ids = {a['task_id'] for a in assignments if a.get('task_id')}
    if len(keep_ids) != sum(bool(a.get('task_id')) for a in assignments):
        raise ValidationError('Uma etapa foi enviada mais de uma vez.')
    if keep_ids - set(content.tasks.filter(pk__in=keep_ids).values_list('pk', flat=True)):
        raise ValidationError('Uma etapa não pertence a esta demanda.')
    if keep_ids:
        content.tasks.exclude(pk__in=keep_ids).delete()
    else:
        content.tasks.all().delete()

    for order, data in enumerate(assignments):
        offset = data.get('due_offset_days')
        due_dt = _task_due_datetime(content, offset)
        task_id = data.get('task_id')
        if task_id:
            task = MediaTask.objects.filter(pk=task_id, content=content).first()
            if not task:
                continue
            task.title = data['role']
            task.assigned_to_id = data.get('user_id')
            task.due_offset_days = offset
            task.due_date = due_dt
            task.description = data.get('description', '')
            task.sort_order = order
            task.save(update_fields=[
                'title', 'assigned_to_id', 'due_offset_days', 'due_date',
                'description', 'sort_order', 'update_at',
            ])
        else:
            MediaTask.objects.create(
                content=content,
                title=data['role'],
                assigned_to_id=data.get('user_id'),
                due_offset_days=offset,
                due_date=due_dt,
                description=data.get('description', ''),
                sort_order=order,
            )

    tasks = list(content.tasks.select_related('assigned_to__member').order_by('sort_order', 'pk'))
    first_assigned = next((t for t in tasks if t.assigned_to_id), None)
    earliest = next((t.due_date for t in tasks if t.due_date), None)
    content.responsible_id = first_assigned.assigned_to_id if first_assigned else None
    fields = ['responsible_id', 'update_at']
    # An existing demand deadline is independent from its execution steps.
    if content.due_date is None and content.due_offset_days is None and not content.due_date_auto and earliest:
        content.due_date = earliest
        fields.append('due_date')
    content.save(update_fields=fields)
    sync_content_status_from_tasks(content)


def build_template_item_assignment_rows(item):
    """Linhas iniciais para etapas de uma demanda padrão."""
    steps = list(item.steps.order_by('sort_order', 'pk'))
    if steps:
        rows = []
        for step in steps:
            offset_parts = decompress_assignment_offset(step.due_offset_days)
            rows.append({
                'id': step.pk,
                'role': step.title,
                'user_id': '',
                'due_days': offset_parts['due_days'],
                'due_relation': offset_parts['due_relation'],
                'description': step.description or '',
                'is_custom_role': step.title not in ASSIGNMENT_ROLE_PRESETS,
            })
        return rows
    return [{
        'id': '', 'role': '', 'user_id': '', 'due_days': '', 'due_relation': 'before',
        'description': '', 'is_custom_role': False,
    }]


@transaction.atomic
def sync_template_item_assignments(item, assignments):
    """Sincroniza etapas sugeridas de uma demanda padrão."""
    keep_ids = {a['task_id'] for a in assignments if a.get('task_id')}
    if len(keep_ids) != sum(bool(a.get('task_id')) for a in assignments):
        raise ValidationError('Uma etapa foi enviada mais de uma vez.')
    from website.models.media_event_type import MediaPlanningTemplateItemStep
    if keep_ids:
        item.steps.exclude(pk__in=keep_ids).delete()
    else:
        item.steps.all().delete()

    for order, data in enumerate(assignments):
        step_id = data.get('task_id')
        payload = {
            'title': data['role'],
            'due_offset_days': data.get('due_offset_days'),
            'description': data.get('description', ''),
            'sort_order': order,
        }
        if step_id:
            step = MediaPlanningTemplateItemStep.objects.filter(pk=step_id, template_item=item).first()
            if not step:
                continue
            for field, value in payload.items():
                setattr(step, field, value)
            step.save(update_fields=[*payload.keys(), 'update_at'])
        else:
            MediaPlanningTemplateItemStep.objects.create(template_item=item, **payload)


def build_assignment_rows(content):
    """Linhas iniciais para o formulário de etapas."""
    tasks = list(content.tasks.order_by('sort_order', 'pk'))
    if tasks:
        rows = []
        for t in tasks:
            offset_parts = _task_offset_for_form(t, content)
            rows.append({
                'id': t.pk,
                'role': t.title,
                'user_id': t.assigned_to_id or '',
                'due_days': offset_parts['due_days'],
                'due_relation': offset_parts['due_relation'],
                'description': t.description or '',
                'is_custom_role': t.title not in ASSIGNMENT_ROLE_PRESETS,
            })
        return rows
    if content.responsible_id or content.due_offset_days is not None:
        offset_parts = decompress_assignment_offset(content.due_offset_days)
        return [{
            'id': '',
            'role': 'Responsável',
            'user_id': content.responsible_id or '',
            'due_days': offset_parts['due_days'],
            'due_relation': offset_parts['due_relation'],
            'description': '',
            'is_custom_role': False,
        }]
    return [{
        'id': '', 'role': '', 'user_id': '', 'due_days': '', 'due_relation': 'before',
        'description': '', 'is_custom_role': False,
    }]


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

    events_qs = Event.objects.filter(is_recurring=False).select_related('event_type').annotate(
        content_count=Count('media_contents', filter=Q(media_contents__deleted__isnull=True), distinct=True),
        done_count=Count('media_contents', filter=Q(media_contents__status__in=DONE_STATUSES, media_contents__deleted__isnull=True), distinct=True),
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
    event = Event.objects.select_related('event_type').get(pk=event_pk, is_recurring=False)
    contents = list(
        MediaContent.objects.filter(event=event)
        .select_related('responsible__member', 'sub_team__ministry', 'assigned_role')
        .prefetch_related(
            Prefetch('tasks', queryset=MediaTask.objects.select_related('assigned_to__member').order_by('sort_order', 'pk'))
        )
        .order_by('due_date', 'publication_date', 'title')
    )
    attach_assignment_warnings(contents)
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
        'responsible__member', 'sub_team__ministry', 'assigned_role'
    ).prefetch_related(
        Prefetch('tasks', queryset=MediaTask.objects.select_related('assigned_to__member').order_by('sort_order', 'pk'))
    ).get(pk=content_pk, event__isnull=True)
    attach_assignment_warnings([content])
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
            ministry=media_ministry(),
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
        'teams': media_teams().filter(is_active=True).order_by('name'),
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
