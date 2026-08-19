from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from ...models.media_content import MediaContent
from ...models.media_task import MediaTask, TASK_STATUS_CHOICES
from ...services.demands_hub import (
    DEMAND_QUICK_TYPES,
    DEMAND_TYPES_ORGANIZATIONAL,
    DEMAND_TYPES_TECHNICAL,
    ASSIGNMENT_ROLE_PRESETS,
    build_assignment_rows,
    build_event_detail,
    build_free_detail,
    build_sidebar_items,
    content_hub_url,
    get_demand_type_roles_map,
    get_filter_context,
    get_responsible_picker_options,
    hub_redirect_url,
    parse_selected,
)
from ...services.media_planning import get_available_template_items, get_template_for_event
from ...forms.media_planning import (
    MediaContentForm,
    MediaDemandQuickForm,
    MediaEventQuickForm,
    MediaEventTypeQuickForm,
    MediaTaskForm,
)
from .mixins import MediaLeaderRequiredMixin, MediaMemberRequiredMixin


class MediaContentListView(MediaMemberRequiredMixin, View):
    """Hub Demandas e Eventos — lista + master-detail."""

    template_name = 'member/media_planning/demands_hub.html'

    def get(self, request):
        create_mode = request.GET.get('create', '')
        if create_mode not in ('demand', 'event'):
            create_mode = ''

        selected = request.GET.get('selected', '')
        sidebar_months = build_sidebar_items(request)
        filters = get_filter_context()

        if not create_mode and not selected and sidebar_months:
            first_month = sidebar_months[0]
            if first_month.get('items') and request.GET.get('auto_select', '1') != '0':
                selected = first_month['items'][0]['key']

        kind, pk = parse_selected(selected)
        detail = None
        detail_type = None
        event_template = None
        available_template_count = 0

        if not create_mode and kind == 'event' and pk:
            try:
                detail = build_event_detail(pk)
                detail_type = 'event'
                event_template = get_template_for_event(detail['event'])
                available_template_count = get_available_template_items(detail['event']).count()
            except Exception:
                selected = ''
                detail = None
        elif not create_mode and kind == 'free' and pk:
            try:
                detail = build_free_detail(pk)
                detail_type = 'free'
            except Exception:
                selected = ''
                detail = None

        demand_form = MediaDemandQuickForm(prefix='demand')
        event_form = MediaEventQuickForm(prefix='event')
        event_type_quick_form = MediaEventTypeQuickForm(prefix='event_type')
        event_types = list(event_form.fields['event_type'].queryset)
        edit_form = MediaDemandQuickForm(prefix='edit')
        edit_assignments = [{'id': '', 'role': '', 'user_id': '', 'due_date': ''}]
        if detail_type == 'free' and detail:
            edit_form = MediaDemandQuickForm(instance=detail['content'], prefix='edit')
            edit_assignments = build_assignment_rows(detail['content'])

        ctx = {
            **self._nav_context(),
            **filters,
            'create_mode': create_mode,
            'demand_form': demand_form,
            'event_form': event_form,
            'event_type_quick_form': event_type_quick_form,
            'event_types': event_types,
            'edit_form': edit_form,
            'edit_assignments': edit_assignments,
            'demand_assignments': [{'id': '', 'role': '', 'user_id': '', 'due_date': '', 'description': '', 'is_custom_role': False}],
            'blank_assignment': {'id': '', 'role': '', 'user_id': '', 'due_date': '', 'description': '', 'is_custom_role': False},
            'demand_type_roles': get_demand_type_roles_map(),
            'assignment_role_presets': ASSIGNMENT_ROLE_PRESETS,
            'edit_mode': request.GET.get('edit', '') == '1',
            'hub_url': hub_redirect_url(request),
            'demand_quick_types': DEMAND_QUICK_TYPES,
            'demand_types_technical': DEMAND_TYPES_TECHNICAL,
            'demand_types_organizational': DEMAND_TYPES_ORGANIZATIONAL,
            'responsible_options': get_responsible_picker_options(),
            'sidebar_months': sidebar_months,
            'selected': selected,
            'detail': detail,
            'detail_type': detail_type,
            'event_template': event_template,
            'available_template_count': available_template_count,
            'filter_kind': request.GET.get('kind', 'all'),
            'filter_q': request.GET.get('q', ''),
            'filter_responsible': request.GET.get('responsible', ''),
            'filter_team': request.GET.get('team', ''),
            'filter_event_type': request.GET.get('event_type', ''),
            'filter_period_from': request.GET.get('period_from', ''),
            'filter_period_to': request.GET.get('period_to', ''),
        }
        return render(request, self.template_name, ctx)


class MediaDemandsPanelView(MediaMemberRequiredMixin, View):
    """Retorna HTML parcial do painel de detalhe (desktop AJAX)."""

    def get(self, request):
        kind, pk = parse_selected(request.GET.get('selected', ''))
        event_template = None
        available_template_count = 0

        if kind == 'event' and pk:
            detail = build_event_detail(pk)
            event_template = get_template_for_event(detail['event'])
            available_template_count = get_available_template_items(detail['event']).count()
            return render(request, 'member/media_planning/partials/demands_detail_event.html', {
                **self._nav_context(),
                'detail': detail,
                'event_template': event_template,
                'available_template_count': available_template_count,
                'selected': request.GET.get('selected'),
            })
        if kind == 'free' and pk:
            detail = build_free_detail(pk)
            return render(request, 'member/media_planning/partials/demands_detail_free.html', {
                **self._nav_context(),
                'detail': detail,
                'selected': request.GET.get('selected'),
                'hub_url': hub_redirect_url(request),
                'filter_kind': request.GET.get('kind', 'all'),
            })
        return render(request, 'member/media_planning/partials/demands_empty.html', {
            **self._nav_context(),
        })


class MediaContentCreateView(MediaLeaderRequiredMixin, View):
    template_name = 'member/media_planning/content_form.html'

    def get(self, request):
        initial = {}
        month_plan_pk = request.GET.get('month_plan', '')
        event_pk = request.GET.get('event', '')
        category_pk = request.GET.get('category', '')  # MediaContentCategory PK
        content_type = request.GET.get('content_type', '')

        if event_pk:
            initial['event'] = event_pk
        if content_type:
            initial['content_type'] = content_type
        if category_pk:
            try:
                from ...models.media_month_plan import MediaContentCategory
                cat = MediaContentCategory.objects.get(pk=category_pk)
                initial['content_type'] = cat.content_type
            except Exception:
                pass

        ctx = {
            **self._nav_context(),
            'form': MediaContentForm(initial=initial),
            'action': 'create',
            '_month_plan_pk': month_plan_pk,
            '_category_pk': category_pk,
            'prefill_event': event_pk,
        }
        return render(request, self.template_name, ctx)

    def post(self, request):
        form = MediaContentForm(request.POST)
        if form.is_valid():
            content = form.save(commit=False)
            month_plan_pk = request.POST.get('_month_plan_pk', '')
            category_pk = request.POST.get('_category_pk', '')
            if month_plan_pk:
                from ...models.media_month_plan import MediaMonthPlan
                try:
                    content.month_plan = MediaMonthPlan.objects.get(pk=month_plan_pk)
                except MediaMonthPlan.DoesNotExist:
                    pass
            if category_pk:
                from ...models.media_month_plan import MediaContentCategory
                try:
                    content.category = MediaContentCategory.objects.get(pk=category_pk)
                except MediaContentCategory.DoesNotExist:
                    pass
            content.save()
            messages.success(request, f'Conteúdo "{content.title}" criado com sucesso!')
            if content.month_plan_id:
                plan = content.month_plan
                if content.event_id:
                    return redirect(
                        'media_plan_macro_event',
                        year=plan.year,
                        month=plan.month,
                        event_pk=content.event_id,
                    )
                if content.category_id:
                    return redirect(
                        'media_plan_macro_category',
                        year=plan.year,
                        month=plan.month,
                        category_pk=content.category_id,
                    )
                return redirect('media_plan_step', year=plan.year, month=plan.month, step=2)
            if content.event_id:
                return redirect(f"{reverse('media_content_list')}?selected=event-{content.event_id}")
            return redirect('media_category_contents', content_type=content.content_type)
        ctx = {
            **self._nav_context(),
            'form': form,
            'action': 'create',
            '_month_plan_pk': request.POST.get('_month_plan_pk', ''),
            '_category_pk': request.POST.get('_category_pk', ''),
        }
        return render(request, self.template_name, ctx)


class MediaContentUpdateView(MediaLeaderRequiredMixin, View):
    template_name = 'member/media_planning/content_form.html'

    def get(self, request, pk):
        content = get_object_or_404(MediaContent, pk=pk)
        ctx = {
            **self._nav_context(),
            'form': MediaContentForm(instance=content),
            'content': content,
            'action': 'edit',
        }
        return render(request, self.template_name, ctx)

    def post(self, request, pk):
        content = get_object_or_404(MediaContent, pk=pk)
        form = MediaContentForm(request.POST, instance=content)
        if form.is_valid():
            content = form.save()
            messages.success(request, f'Conteúdo "{content.title}" atualizado!')
            return redirect(content_hub_url(content))
        ctx = {
            **self._nav_context(),
            'form': form,
            'content': content,
            'action': 'edit',
        }
        return render(request, self.template_name, ctx)


class MediaContentDeleteView(MediaLeaderRequiredMixin, View):
    def post(self, request, pk):
        content = get_object_or_404(MediaContent, pk=pk)
        title = content.title
        content.delete()
        messages.success(request, f'Conteúdo "{title}" excluído.')
        next_url = request.POST.get('next')
        if next_url:
            return redirect(next_url)
        return redirect('media_content_list')


class MediaTaskCreateView(MediaLeaderRequiredMixin, View):
    def post(self, request, pk):
        content = get_object_or_404(MediaContent, pk=pk)
        form = MediaTaskForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
            task.content = content
            task.save()
            messages.success(request, f'Tarefa "{task.title}" criada!')
        else:
            messages.error(request, 'Erro ao criar a tarefa. Verifique os campos.')
        return redirect(content_hub_url(content))


class MediaTaskUpdateStatusView(MediaMemberRequiredMixin, View):
    def post(self, request, pk):
        task = get_object_or_404(MediaTask, pk=pk)
        new_status = request.POST.get('status', '')
        if new_status == 'toggle':
            new_status = 'pending' if task.status == 'completed' else 'completed'
        valid_statuses = [c[0] for c in TASK_STATUS_CHOICES]
        if new_status in valid_statuses:
            task.status = new_status
            task.save(update_fields=['status', 'update_at'])
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'status': new_status,
                    'label': task.get_status_display(),
                })
            messages.success(
                request,
                f'Status atualizado para "{task.get_status_display()}".',
            )
        next_url = request.POST.get('next')
        if next_url:
            return redirect(next_url)
        content = get_object_or_404(MediaContent, pk=task.content_id)
        return redirect(content_hub_url(content))


class MediaTaskUpdateDescriptionView(MediaMemberRequiredMixin, View):
    def post(self, request, pk):
        from django.utils.html import linebreaks, urlize
        from django.utils.safestring import mark_safe

        task = get_object_or_404(MediaTask, pk=pk)
        description = request.POST.get('description', '').strip()
        task.description = description
        task.save(update_fields=['description', 'update_at'])
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            html = mark_safe(linebreaks(urlize(description))) if description else ''
            return JsonResponse({'description': description, 'description_html': html})
        next_url = request.POST.get('next')
        if next_url:
            return redirect(next_url)
        content = get_object_or_404(MediaContent, pk=task.content_id)
        return redirect(content_hub_url(content))


class MediaCommentCreateView(MediaMemberRequiredMixin, View):
    def post(self, request, pk):
        content = get_object_or_404(MediaContent, pk=pk)
        from ...forms.media_planning import MediaCommentForm as _Form
        form = _Form(request.POST)
        if form.is_valid():
            comment = form.save(commit=False)
            comment.author = request.user
            comment.content = content
            comment.save()
        else:
            messages.error(request, 'Não foi possível adicionar o comentário.')
        return redirect(content_hub_url(content))


class MediaAttachmentCreateView(MediaMemberRequiredMixin, View):
    def post(self, request, pk):
        content = get_object_or_404(MediaContent, pk=pk)
        from ...forms.media_planning import MediaAttachmentForm as _Form
        form = _Form(request.POST, request.FILES)
        if form.is_valid():
            attachment = form.save(commit=False)
            attachment.content = content
            attachment.uploaded_by = request.user
            attachment.save()
            messages.success(request, f'Arquivo "{attachment.name}" anexado!')
        else:
            messages.error(request, 'Erro ao enviar o arquivo. Verifique o tipo e tamanho.')
        return redirect(content_hub_url(content))
