import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect

from ...models import MonthlySchedule, ScheduleDay
from ...models.schedule import DivisionMember, ScaleDivision


@login_required
def division_list_view(request, ministry_id):
    """Redireciona para listagem de escalas (divisões agora são por escala)."""
    return redirect('schedule_list')


@login_required
def division_create_view(request, schedule_id):
    """Cria uma nova divisão para uma escala (AJAX)."""
    schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        parent_id = request.POST.get('parent') or None
        order = request.POST.get('order', 0)

        if not name:
            return JsonResponse({'success': False, 'error': 'Nome obrigatório'}, status=400)

        try:
            division = ScaleDivision(
                name=name,
                schedule=schedule,
                parent_id=parent_id,
                order=int(order),
            )
            division.save()
            return JsonResponse({
                'success': True,
                'division_id': division.id,
                'name': division.name,
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


@login_required
def division_edit_view(request, division_id):
    """Edita uma divisão (AJAX)."""
    division = get_object_or_404(ScaleDivision, id=division_id, deleted__isnull=True)

    if request.method == 'POST':
        division.name = request.POST.get('name', '').strip()
        parent_id = request.POST.get('parent')
        division.parent_id = parent_id if parent_id else None
        division.order = int(request.POST.get('order', 0))
        division.is_active = request.POST.get('is_active') == 'true'

        try:
            division.save()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


@login_required
def division_delete_view(request, division_id):
    """Deleta uma divisão."""
    if request.method == 'POST':
        division = get_object_or_404(ScaleDivision, id=division_id, deleted__isnull=True)
        division.delete()
        return JsonResponse({'success': True})

    return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


@login_required
def schedule_day_division_assign_view(request, day_id):
    """Atribui membros a divisões em um dia de escala."""
    day = get_object_or_404(ScheduleDay, id=day_id, deleted__isnull=True)

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'JSON inválido'}, status=400)

        assignments = data.get('assignments', [])

        # Limpar atribuições existentes para este dia
        DivisionMember.objects.filter(
            schedule_day=day,
            deleted__isnull=True,
        ).delete()

        # Criar novas atribuições
        created = []
        for item in assignments:
            division_id = item.get('division_id')
            member_ids = item.get('member_ids', [])

            division = get_object_or_404(
                ScaleDivision, id=division_id, deleted__isnull=True,
            )

            for member_id in member_ids:
                dm = DivisionMember.objects.create(
                    division=division,
                    schedule_day=day,
                    member_id=member_id,
                )
                created.append(dm.id)

        return JsonResponse({
            'success': True,
            'created_count': len(created),
        })

    # GET: retorna atribuições atuais
    assignments = DivisionMember.objects.filter(
        schedule_day=day,
        deleted__isnull=True,
    ).select_related('division', 'member').order_by('division__order', 'member__name')

    divisions = ScaleDivision.objects.filter(
        schedule=day.schedule,
        is_active=True,
        deleted__isnull=True,
    ).order_by('order', 'name')

    return JsonResponse({
        'day_id': day.id,
        'date': day.date.strftime('%d/%m/%Y'),
        'divisions': [
            {
                'id': d.id,
                'name': d.name,
                'parent_id': d.parent_id,
                'order': d.order,
            }
            for d in divisions
        ],
        'assignments': [
            {
                'id': a.id,
                'division_id': a.division_id,
                'division_name': a.division.name,
                'member_id': a.member_id,
                'member_name': a.member.name,
            }
            for a in assignments
        ],
    })
