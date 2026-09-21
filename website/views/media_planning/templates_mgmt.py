from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from ...forms.media_organization import MediaPlanningTemplateItemForm, MediaTemplateUnifiedForm
from ...models.media_event_type import (
    MediaEventType,
    MediaPlanningTemplate,
    MediaPlanningTemplateItem,
    MediaPlanningTemplateItemStep,
)
from ...services.demands_hub import (
    ASSIGNMENT_ROLE_PRESETS,
    DEMAND_QUICK_TYPES,
    DEMAND_TYPES_ORGANIZATIONAL,
    DEMAND_TYPES_TECHNICAL,
    build_template_item_assignment_rows,
    decompress_assignment_offset,
    extra_demand_type_options_for,
    get_demand_type_roles_map,
    parse_demand_assignments,
    sync_template_item_assignments,
)
from .event_types import event_type_list_context
from .mixins import MediaLeaderRequiredMixin, MediaMemberRequiredMixin

_BLANK_ASSIGNMENT = {
    'id': '', 'role': '', 'user_id': '', 'due_days': '', 'due_relation': 'before',
    'description': '', 'is_custom_role': False,
}


def _failed_assignment_rows(assignments):
    rows = []
    for row in assignments:
        offset_parts = decompress_assignment_offset(row.get('due_offset_days'))
        rows.append({
            **row,
            'id': row.get('task_id', ''),
            'due_days': offset_parts['due_days'],
            'due_relation': offset_parts['due_relation'],
            'is_custom_role': row.get('role') not in ASSIGNMENT_ROLE_PRESETS,
        })
    return rows or [_BLANK_ASSIGNMENT.copy()]


def _detail_close_url(template, search=''):
    url = reverse('media_template_detail', args=[template.pk])
    if search:
        url += f'?search={search}'
    return url


def _detail_edit_url(template, item_pk, search=''):
    url = reverse('media_template_detail', args=[template.pk]) + f'?edit={item_pk}'
    if search:
        url += f'&search={search}'
    return url


def template_detail_context(
    request,
    template,
    create_form=None,
    create_assignments=None,
    edit_form=None,
    edit_assignments=None,
    edit_item=None,
):
    """Dados da tela de demandas padrão e dos modais de criação/edição."""
    search = request.GET.get('search', '').strip()
    items = template.items.select_related('default_sub_team').prefetch_related(
        Prefetch('steps', queryset=MediaPlanningTemplateItemStep.objects.order_by('sort_order', 'pk'))
    )
    if search:
        items = items.filter(
            Q(title__icontains=search)
            | Q(description__icontains=search)
            | Q(notes__icontains=search)
        )

    show_edit_modal = edit_form is not None

    if not show_edit_modal:
        edit_pk = request.GET.get('edit', '').strip()
        if edit_pk.isdigit():
            edit_item = items.filter(pk=int(edit_pk)).first()
            if edit_item:
                edit_form = MediaPlanningTemplateItemForm(instance=edit_item, prefix='template')
                edit_assignments = build_template_item_assignment_rows(edit_item)
                show_edit_modal = True

    show_create_modal = (create_form is not None or request.GET.get('create') == '1') and not show_edit_modal

    shared = {
        'blank_assignment': _BLANK_ASSIGNMENT,
        'assignment_role_presets': ASSIGNMENT_ROLE_PRESETS,
        'demand_type_roles': get_demand_type_roles_map(),
        'demand_quick_types': DEMAND_QUICK_TYPES,
        'demand_types_technical': DEMAND_TYPES_TECHNICAL,
        'demand_types_organizational': DEMAND_TYPES_ORGANIZATIONAL,
        'prefix': 'template',
        'detail_close_url': _detail_close_url(template, search),
    }
    content_types = []
    if show_create_modal:
        content_types.append(getattr((create_form or MediaPlanningTemplateItemForm(prefix='template')).instance, 'content_type', None))
    if show_edit_modal and edit_item:
        content_types.append(edit_item.content_type)
    shared['extra_demand_type_options'] = extra_demand_type_options_for(*content_types)

    ctx = {
        'template': template,
        'event_type': template.event_type,
        'items': items,
        'search': search,
        'show_create_modal': show_create_modal,
        'show_edit_modal': show_edit_modal,
        **shared,
    }

    if show_create_modal:
        ctx['create_form'] = create_form or MediaPlanningTemplateItemForm(prefix='template')
        ctx['create_assignments'] = (
            create_assignments if create_assignments is not None else [_BLANK_ASSIGNMENT.copy()]
        )
        ctx['create_post_url'] = reverse('media_template_item_create', args=[template.pk])
    if show_edit_modal and edit_form:
        ctx['edit_form'] = edit_form
        ctx['edit_item'] = edit_item
        ctx['edit_assignments'] = (
            edit_assignments if edit_assignments is not None else build_template_item_assignment_rows(edit_item)
        )
        ctx['edit_close_url'] = _detail_close_url(template, search)
        ctx['edit_post_url'] = reverse('media_template_item_edit', args=[edit_item.pk])

    return ctx


class MediaTemplateListView(MediaMemberRequiredMixin, View):
    def get(self, request):
        return redirect(f"{reverse('media_content_list')}?tab=templates")


class MediaTemplateCreateView(MediaLeaderRequiredMixin, View):
    def get(self, request):
        return redirect(f"{reverse('media_event_type_list')}?create=1")

    def post(self, request):
        form = MediaTemplateUnifiedForm(request.POST)
        if form.is_valid():
            tpl = form.save()
            messages.success(request, f'Template "{tpl.name}" criado!')
            return redirect('media_event_type_list')
        return render(request, 'member/media_planning/event_type_list.html', {
            **self._nav_context(),
            **event_type_list_context(request, create_form=form),
        })


class MediaTemplateSetupView(MediaLeaderRequiredMixin, View):
    """Garante template para um tipo existente e abre o detalhe."""

    def get(self, request, event_type_pk):
        event_type = get_object_or_404(MediaEventType, pk=event_type_pk)
        tpl, created = MediaPlanningTemplate.objects.get_or_create(
            event_type=event_type,
            defaults={
                'name': event_type.name,
                'description': event_type.description,
                'is_active': event_type.is_active,
            },
        )
        if created:
            messages.info(request, f'Template "{event_type.name}" configurado.')
        return redirect('media_template_detail', pk=tpl.pk)


class MediaTemplateDetailView(MediaMemberRequiredMixin, View):
    template_name = 'member/media_planning/template_detail.html'

    def get(self, request, pk):
        template = get_object_or_404(
            MediaPlanningTemplate.objects.select_related('event_type'),
            pk=pk,
        )
        ctx = {
            **self._nav_context(),
            **template_detail_context(request, template),
        }
        return render(request, self.template_name, ctx)


class MediaTemplateUpdateView(MediaLeaderRequiredMixin, View):
    template_name = 'member/media_planning/template_form.html'

    def get(self, request, pk):
        template = get_object_or_404(
            MediaPlanningTemplate.objects.select_related('event_type'),
            pk=pk,
        )
        ctx = {
            **self._nav_context(),
            'template': template,
            'form': MediaTemplateUnifiedForm(instance=template),
            'action': 'edit',
        }
        return render(request, self.template_name, ctx)

    def post(self, request, pk):
        template = get_object_or_404(MediaPlanningTemplate, pk=pk)
        form = MediaTemplateUnifiedForm(request.POST, instance=template)
        if form.is_valid():
            form.save()
            messages.success(request, f'Template "{template.name}" atualizado!')
            return redirect('media_template_detail', pk=template.pk)
        ctx = {
            **self._nav_context(),
            'template': template,
            'form': form,
            'action': 'edit',
        }
        return render(request, self.template_name, ctx)


class MediaTemplateItemCreateView(MediaLeaderRequiredMixin, View):
    def get(self, request, template_pk):
        return redirect(f"{reverse('media_template_detail', args=[template_pk])}?create=1")

    def post(self, request, template_pk):
        template = get_object_or_404(MediaPlanningTemplate, pk=template_pk)
        form = MediaPlanningTemplateItemForm(request.POST, prefix='template')
        assignments = parse_demand_assignments(request.POST, prefix='template')
        if form.is_valid():
            try:
                with transaction.atomic():
                    item = form.save(commit=False)
                    item.template = template
                    item.save()
                    sync_template_item_assignments(item, assignments)
            except ValidationError as exc:
                form.add_error(None, ' '.join(exc.messages))
            else:
                messages.success(request, f'Demanda "{item.title}" adicionada!')
                return redirect('media_template_detail', pk=template.pk)
        return render(request, 'member/media_planning/template_detail.html', {
            **self._nav_context(),
            **template_detail_context(
                request, template, create_form=form,
                create_assignments=_failed_assignment_rows(assignments),
            ),
        })


class MediaTemplateItemUpdateView(MediaLeaderRequiredMixin, View):
    def get(self, request, pk):
        item = get_object_or_404(MediaPlanningTemplateItem.objects.select_related('template'), pk=pk)
        search = request.GET.get('search', '').strip()
        return redirect(_detail_edit_url(item.template, item.pk, search))

    def post(self, request, pk):
        item = get_object_or_404(MediaPlanningTemplateItem.objects.select_related('template'), pk=pk)
        form = MediaPlanningTemplateItemForm(request.POST, instance=item, prefix='template')
        assignments = parse_demand_assignments(request.POST, prefix='template')
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                    sync_template_item_assignments(item, assignments)
            except ValidationError as exc:
                form.add_error(None, ' '.join(exc.messages))
            else:
                messages.success(request, f'Demanda "{item.title}" atualizada!')
                return redirect('media_template_detail', pk=item.template_id)
        return render(request, 'member/media_planning/template_detail.html', {
            **self._nav_context(),
            **template_detail_context(
                request, item.template,
                edit_form=form,
                edit_item=item,
                edit_assignments=_failed_assignment_rows(assignments),
            ),
        })


class MediaTemplateItemDeleteView(MediaLeaderRequiredMixin, View):
    def post(self, request, pk):
        item = get_object_or_404(MediaPlanningTemplateItem.objects.select_related('template'), pk=pk)
        template_pk = item.template_id
        title = item.title
        item.delete()
        messages.success(request, f'Demanda "{title}" removida!')
        return redirect('media_template_detail', pk=template_pk)
