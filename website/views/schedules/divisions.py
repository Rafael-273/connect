import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views import View

from ..mixins import StaffRequiredMixin
from ...models import MonthlySchedule, ScheduleDay
from ...models.schedule import DivisionMember, ScaleDivision


class DivisionListView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Redireciona para listagem de escalas (divisões agora são por escala)."""

    def get(self, request, ministry_id):
        return redirect('schedule_list')


class DivisionCreateView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Cria uma nova divisão para uma escala (AJAX)."""

    def post(self, request, schedule_id):
        schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)
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

    def get(self, request, schedule_id):
        return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


class DivisionEditView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Edita uma divisão (AJAX)."""

    def post(self, request, division_id):
        division = get_object_or_404(ScaleDivision, id=division_id, deleted__isnull=True)
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

    def get(self, request, division_id):
        return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


class DivisionDeleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Deleta uma divisão."""

    def post(self, request, division_id):
        division = get_object_or_404(ScaleDivision, id=division_id, deleted__isnull=True)
        division.delete()
        return JsonResponse({'success': True})

    def get(self, request, division_id):
        return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


class ScheduleDayDivisionAssignView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Atribui membros a divisões em um dia de escala."""

    def get(self, request, day_id):
        day = get_object_or_404(ScheduleDay, id=day_id, deleted__isnull=True)

        assignments = (
            DivisionMember.objects
            .filter(schedule_day=day, deleted__isnull=True)
            .select_related('division', 'member')
            .order_by('division__order', 'member__name')
        )
        divisions = (
            ScaleDivision.objects
            .filter(schedule=day.schedule, is_active=True, deleted__isnull=True)
            .order_by('order', 'name')
        )

        return JsonResponse({
            'day_id': day.id,
            'date': day.date.strftime('%d/%m/%Y'),
            'has_shifts': day.has_shifts,
            'divisions': [
                {'id': d.id, 'name': d.name, 'parent_id': d.parent_id, 'order': d.order}
                for d in divisions
            ],
            'assignments': [
                {
                    'id': a.id,
                    'division_id': a.division_id,
                    'division_name': a.division.name,
                    'member_id': a.member_id,
                    'member_name': a.member.name,
                    'shift': a.shift,
                }
                for a in assignments
            ],
        })

    def post(self, request, day_id):
        day = get_object_or_404(ScheduleDay, id=day_id, deleted__isnull=True)

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'JSON inválido'}, status=400)

        DivisionMember.objects.filter(schedule_day=day, deleted__isnull=True).delete()

        created = []
        for item in data.get('assignments', []):
            division_id = item.get('division_id')
            member_ids = item.get('member_ids', [])
            shift = item.get('shift', 'none')
            if shift not in ('none', 'morning', 'evening'):
                shift = 'none'

            division = get_object_or_404(ScaleDivision, id=division_id, deleted__isnull=True)

            for member_id in member_ids:
                dm = DivisionMember.objects.create(
                    division=division,
                    schedule_day=day,
                    member_id=member_id,
                    shift=shift,
                )
                created.append(dm.id)

        return JsonResponse({'success': True, 'created_count': len(created)})
