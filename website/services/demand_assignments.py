"""Assignment validation shared by forms, models and the demand hub."""
from django.core.exceptions import ValidationError


def _media_ministry_user_ids():
    from website.services.demands_hub import get_responsible_picker_options
    return set(get_responsible_picker_options().values_list('pk', flat=True))


def validate_ministry_assignees(user_ids):
    """Garante que responsáveis pertencem ao ministério de Mídia Externa."""
    user_ids = {int(value) for value in user_ids if value}
    if not user_ids:
        return
    allowed = _media_ministry_user_ids()
    if user_ids - allowed:
        raise ValidationError(
            'Todos os responsáveis devem ser membros ativos do ministério de Mídia Externa.'
        )


def assignment_warning(content):
    user_ids = {
        uid for uid in [content.responsible_id] + [task.assigned_to_id for task in content.tasks.all()]
        if uid
    }
    if user_ids and user_ids - _media_ministry_user_ids():
        return 'Algum responsável não está mais ativo no ministério. Revise as atribuições.'
    if content.sub_team_id:
        team = content.sub_team
        if not team.is_active or team.deleted or not team.ministry.is_active or team.ministry.deleted:
            return 'A equipe responsável não está mais ativa. Revise a demanda.'
    return ''


def attach_assignment_warnings(contents):
    allowed = _media_ministry_user_ids()
    for content in contents:
        content.assignment_warning = ''
        ids = {
            uid for uid in [content.responsible_id] + [t.assigned_to_id for t in content.tasks.all()]
            if uid
        }
        if ids - allowed:
            content.assignment_warning = (
                'Algum responsável não está mais ativo no ministério. Revise as atribuições.'
            )
            continue
        if content.sub_team_id:
            team = content.sub_team
            if not team.is_active or not team.ministry.is_active:
                content.assignment_warning = (
                    'A equipe responsável não está mais ativa. Revise a demanda.'
                )
