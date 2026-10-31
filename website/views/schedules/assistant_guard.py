"""Route assisted schedule edits through the same occurrence-aware validators."""
from django.http import JsonResponse
from django.urls import reverse

from website.models import ScheduleAssistantState


class AssistedScheduleWriteGuardMixin:
    def dispatch(self, request, *args, **kwargs):
        if request.method == 'POST':
            lookup = {}
            if 'schedule_id' in kwargs:
                lookup['schedule_id'] = kwargs['schedule_id']
            elif 'day_id' in kwargs:
                lookup['schedule__days__pk'] = kwargs['day_id']
            elif 'division_id' in kwargs:
                lookup['schedule__divisions__pk'] = kwargs['division_id']
            state = ScheduleAssistantState.objects.filter(status='draft', **lookup).first() if lookup else None
            if state:
                return JsonResponse({'success': False, 'error': 'Esta escala possui ocorrências e bloqueios. Faça os ajustes no Assistente de Escalas.',
                                     'assistant_url': reverse('media_schedule_assistant', args=[state.pk])}, status=409)
        return super().dispatch(request, *args, **kwargs)
