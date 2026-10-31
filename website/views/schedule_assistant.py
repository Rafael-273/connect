import json
from zoneinfo import ZoneInfo

from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views import View

from website.models import MonthlySchedule, ScheduleAssistantState
from website.services.ministry_organization import media_ministry
from website.services.schedule_agenda import MONTHS
from website.services.schedule_assistant import (
    PlanningConflict, ensure_regular_occurrences, mutate, period_from_input,
    serialize, start_draft,
)
from website.services.schedule_visibility import can_manage_media_schedules


class MediaSecretariatMixin(LoginRequiredMixin):
    login_url = 'member_login'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.ministry = media_ministry()
        if not self.ministry or not can_manage_media_schedules(request.user, self.ministry):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def context(self, **kwargs):
        return {'ministry': self.ministry, 'can_manage': True, 'is_media_ministry': True, **kwargs}

    def state(self, pk):
        return get_object_or_404(
            ScheduleAssistantState.objects.select_related('schedule'), pk=pk,
            ministry=self.ministry, schedule__ministry=self.ministry,
            schedule__deleted__isnull=True, status='draft',
        )


class StartForm(forms.Form):
    month = forms.TypedChoiceField(label='Mês', coerce=int, choices=list(enumerate(MONTHS))[1:],
                                  widget=forms.Select(attrs={'class': 'form-input'}))
    year = forms.IntegerField(label='Ano', min_value=2020, max_value=2100,
                              widget=forms.NumberInput(attrs={'class': 'form-input', 'min': 2020, 'max': 2100}))


class MediaSecretariatHome(MediaSecretariatMixin, View):
    def get(self, request):
        return render(request, 'member/schedule_assistant/secretariat.html', self.context())


class MediaScheduleList(MediaSecretariatMixin, View):
    def get(self, request):
        now = timezone.localdate(timezone=ZoneInfo('America/Sao_Paulo'))
        schedules = MonthlySchedule.objects.filter(ministry=self.ministry).select_related('assistant_state').order_by('-year', '-month', '-pk')
        filters = ScheduleListFilter(request.GET, auto_id='schedule-filter-%s')
        if filters.is_valid():
            values = filters.cleaned_data
            if values['q']:
                schedules = schedules.filter(title__icontains=values['q'])
            if values['status'] == 'draft':
                schedules = schedules.filter(assistant_state__status='draft', assistant_state__deleted__isnull=True)
            elif values['status'] == 'registered':
                schedules = schedules.filter(Q(assistant_state__isnull=True) | Q(assistant_state__status='published'))
            if values['month']:
                schedules = schedules.filter(month=values['month'])
            if values['year']:
                schedules = schedules.filter(year=values['year'])
        page = Paginator(schedules, 20).get_page(request.GET.get('page'))
        query = request.GET.copy()
        query.pop('page', None)
        return render(request, 'member/schedule_assistant/list.html', self.context(
            schedules=page, page_obj=page, filter_form=filters, pagination_query=query.urlencode(),
            has_filters=any(request.GET.get(key) for key in ('q', 'status', 'month', 'year')),
            start_form=StartForm(initial={'month': now.month, 'year': now.year}),
        ))


class ScheduleListFilter(forms.Form):
    q = forms.CharField(label='Pesquisar escalas', required=False, max_length=255,
                        widget=forms.TextInput(attrs={'type': 'search', 'placeholder': 'Buscar escala…', 'class': 'media-search-input', 'autocomplete': 'off'}))
    status = forms.ChoiceField(label='Exibir', required=False, choices=[('', 'Todas as escalas'), ('draft', 'Rascunhos'), ('registered', 'Escalas cadastradas')])
    month = forms.TypedChoiceField(label='Mês', required=False, coerce=int, empty_value=None,
                                  choices=[('', 'Todos os meses'), *list(enumerate(MONTHS))[1:]])
    year = forms.IntegerField(label='Ano', required=False, min_value=2020, max_value=2100,
                             widget=forms.NumberInput(attrs={'placeholder': 'Todos os anos', 'min': 2020, 'max': 2100}))


class ScheduleAssistantStart(MediaSecretariatMixin, View):
    def post(self, request):
        form = StartForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Selecione um mês e um ano válidos, entre 2020 e 2100.')
            return redirect('media_schedule_list')
        state, created = start_draft(self.ministry, period_from_input(**form.cleaned_data), request.user)
        if not created:
            messages.info(request, 'Continuando o rascunho já existente para este mês.')
        return redirect('media_schedule_assistant', pk=state.pk)


@method_decorator(never_cache, name='dispatch')
class ScheduleAssistantPage(MediaSecretariatMixin, View):
    def get(self, request, pk):
        state = ensure_regular_occurrences(self.state(pk))
        return render(request, 'member/schedule_assistant/wizard.html', self.context(
            state=state, initial=serialize(state),
        ))


@method_decorator(never_cache, name='dispatch')
class ScheduleAssistantAPI(MediaSecretariatMixin, View):
    def get(self, request, pk):
        return JsonResponse(serialize(ensure_regular_occurrences(self.state(pk))))

    def post(self, request, pk):
        self.state(pk)
        if len(request.body) > 250000:
            return JsonResponse({'error': 'Solicitação muito grande.'}, status=400)
        try:
            data = json.loads(request.body)
            if not isinstance(data, dict):
                raise ValidationError('Envie um objeto JSON válido.')
            result = mutate(pk, self.ministry, request.user, data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({'error': 'JSON inválido.'}, status=400)
        except PlanningConflict as exc:
            return JsonResponse({'error': str(exc), 'conflict': True}, status=409)
        except ValidationError as exc:
            return JsonResponse({'error': ' '.join(exc.messages)}, status=400)
        if result is None:
            return JsonResponse({'discarded': True, 'redirect_url': reverse('media_schedule_list')})
        return JsonResponse(result)
