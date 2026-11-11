"""Review each current delivery version, preserving an audit of decisions."""
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction

from website.models import MediaContent, MediaDelivery, MediaDeliveryReview, MediaTask
from website.services.demands_hub import sync_content_status_from_tasks


def current_deliveries(task):
    requirements = task.delivery_requirements.filter(active=True).prefetch_related('deliveries__asset').order_by('pk')
    result = []
    for requirement in requirements:
        delivery = next(iter(requirement.deliveries.all()), None)
        result.append((requirement, delivery if delivery and delivery.status != 'withdrawn' else None))
    return result


def review_snapshot(task):
    return [{
        'requirement': requirement.pk, 'format': requirement.format,
        'label': requirement.label, 'required': requirement.required,
        'delivery': delivery.pk if delivery else None,
        'asset': str(delivery.asset_id) if delivery else None,
    } for requirement, delivery in current_deliveries(task)]


def review_token(task):
    return signing.dumps({'task': task.pk, 'snapshot': review_snapshot(task)}, salt='delivery-review')


def update_review_progress(task, *, on_submission=False):
    """A step finishes only after every submitted format is approved."""
    items = current_deliveries(task)
    if not any(requirement.required for requirement, _ in items):
        return
    missing = any(requirement.required and (not delivery or not delivery.asset.ready)
                  for requirement, delivery in items)
    statuses = [delivery.status for _, delivery in items if delivery]
    if missing or 'rejected' in statuses or 'draft' in statuses:
        new_status = 'in_progress'
    elif on_submission or any(status != 'approved' for status in statuses):
        new_status = 'in_review'
    else:
        new_status = 'completed'
    if task.status != new_status:
        task.status = new_status
        task._review_approved = new_status == 'completed'
        task.save(update_fields=['status', 'update_at'])
        sync_content_status_from_tasks(task.content)


def needs_confirmation(task):
    return any(delivery and delivery.status == 'draft' for _, delivery in current_deliveries(task))


def confirm_corrections(task):
    """The caller holds the task and demand locks in a transaction."""
    from website.services.content_deliveries import assert_complete
    assert_complete(task)
    items = current_deliveries(task)
    if any(delivery and delivery.status == 'rejected' for _, delivery in items):
        raise ValidationError('Envie uma nova versão para cada entrega com ajuste solicitado.')
    draft_ids = [delivery.pk for _, delivery in items if delivery and delivery.status == 'draft']
    if not draft_ids:
        raise ValidationError('Não há alterações aguardando envio para revisão.')
    MediaDelivery.objects.filter(pk__in=draft_ids, status='draft').update(status='submitted')
    update_review_progress(task, on_submission=True)


@transaction.atomic
def decide_review(task_id, reviewer, requirement_id, decision, note, token):
    if decision not in ('approved', 'rejected'):
        raise ValidationError('Selecione uma decisão válida.')
    note = note.strip()
    if len(note) > 4000:
        raise ValidationError('A observação deve ter até 4.000 caracteres.')
    try:
        requirement_id = int(requirement_id)
        payload = signing.loads(token, salt='delivery-review', max_age=86400)
    except (signing.BadSignature, ValueError, TypeError):
        raise ValidationError('A revisão expirou. Abra as entregas novamente.')
    task = MediaTask.objects.select_related('content').get(pk=task_id)
    content = MediaContent.objects.select_for_update().get(pk=task.content_id)
    task = MediaTask.objects.select_for_update().get(pk=task_id, content=content)
    snapshot = review_snapshot(task)
    if payload != {'task': task.pk, 'snapshot': snapshot}:
        raise ValidationError('As entregas foram alteradas. Abra a revisão novamente.')
    row = next((item for item in snapshot if item['requirement'] == requirement_id), None)
    if not row or not row['delivery']:
        raise ValidationError('Esta entrega ainda não possui arquivo para revisão.')
    delivery = MediaDelivery.objects.select_for_update().select_related('requirement', 'asset').get(pk=row['delivery'])
    if not delivery.asset.ready:
        raise ValidationError('O arquivo desta entrega não está disponível para revisão.')
    if delivery.status == 'draft':
        raise ValidationError('Aguardando confirmação do responsável para revisar esta versão.')
    if delivery.status != 'submitted':
        raise ValidationError('Esta versão já foi avaliada. Atualize a revisão.')
    MediaDeliveryReview.objects.create(task=task, reviewer=reviewer, requirement=delivery.requirement,
        delivery=delivery, decision=decision, note=note, snapshot=snapshot)
    delivery.status = decision
    delivery.save(update_fields=['status', 'update_at'])
    update_review_progress(task)
    return task
