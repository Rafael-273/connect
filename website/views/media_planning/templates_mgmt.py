from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from ...forms.media_organization import MediaPlanningTemplateItemForm, MediaTemplateUnifiedForm
from ...models.media_event_type import MediaEventType, MediaPlanningTemplate, MediaPlanningTemplateItem
from .event_types import event_type_list_context
from .mixins import MediaLeaderRequiredMixin, MediaMemberRequiredMixin


def template_detail_context(request, template, create_form=None):
    """Dados da tela de demandas padrão e do modal de criação."""
    search = request.GET.get('search', '').strip()
    items = template.items.select_related('default_sub_team', 'default_role')
    if search:
        items = items.filter(
            Q(title__icontains=search)
            | Q(description__icontains=search)
            | Q(notes__icontains=search)
        )
    return {
        'template': template,
        'event_type': template.event_type,
        'items': items,
        'search': search,
        'create_form': create_form or MediaPlanningTemplateItemForm(),
        'show_create_modal': request.GET.get('create') == '1' or create_form is not None,
    }


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
            return redirect('media_template_detail', pk=tpl.pk)
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
            'form': MediaTemplateUnifiedForm(event_type=template.event_type),
            'template': template,
            'action': 'edit',
        }
        return render(request, self.template_name, ctx)

    def post(self, request, pk):
        template = get_object_or_404(
            MediaPlanningTemplate.objects.select_related('event_type'),
            pk=pk,
        )
        form = MediaTemplateUnifiedForm(request.POST, event_type=template.event_type)
        if form.is_valid():
            form.save()
            messages.success(request, f'Template "{template.event_type.name}" atualizado!')
            return redirect('media_template_detail', pk=template.pk)
        ctx = {
            **self._nav_context(),
            'form': form,
            'template': template,
            'action': 'edit',
        }
        return render(request, self.template_name, ctx)


class MediaTemplateItemCreateView(MediaLeaderRequiredMixin, View):
    def get(self, request, template_pk):
        return redirect(f"{reverse('media_template_detail', args=[template_pk])}?create=1")

    def post(self, request, template_pk):
        template = get_object_or_404(MediaPlanningTemplate, pk=template_pk)
        data = request.POST.copy()
        self._normalize_schedule_offsets(data)
        form = MediaPlanningTemplateItemForm(data)
        if form.is_valid():
            item = form.save(commit=False)
            item.template = template
            item.save()
            messages.success(request, f'Demanda "{item.title}" adicionada!')
            return redirect('media_template_detail', pk=template.pk)
        return render(request, 'member/media_planning/template_detail.html', {
            **self._nav_context(),
            **template_detail_context(request, template, create_form=form),
        })

    @staticmethod
    def _normalize_schedule_offsets(data):
        """Converte a escolha amigável de antes/depois no offset assinado do modelo."""
        for field, prefix in (
            ('lead_offset_days', 'lead'),
            ('due_offset_days', 'due'),
            ('publication_offset_days', 'publication'),
        ):
            amount = data.get(f'{prefix}_days', '').strip()
            relation = data.get(f'{prefix}_relation', 'before')
            if not amount:
                data[field] = ''
                continue
            try:
                value = abs(int(amount))
            except (TypeError, ValueError):
                data[field] = amount
                continue
            data[field] = str(-value if relation == 'before' else value)


class MediaTemplateItemUpdateView(MediaLeaderRequiredMixin, View):
    template_name = 'member/media_planning/template_item_form.html'

    def get(self, request, pk):
        item = get_object_or_404(MediaPlanningTemplateItem.objects.select_related('template'), pk=pk)
        ctx = {
            **self._nav_context(),
            'template': item.template,
            'item': item,
            'form': MediaPlanningTemplateItemForm(instance=item),
            'action': 'edit',
        }
        return render(request, self.template_name, ctx)

    def post(self, request, pk):
        item = get_object_or_404(MediaPlanningTemplateItem.objects.select_related('template'), pk=pk)
        form = MediaPlanningTemplateItemForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, f'Demanda "{item.title}" atualizada!')
            return redirect('media_template_detail', pk=item.template_id)
        ctx = {
            **self._nav_context(),
            'template': item.template,
            'item': item,
            'form': form,
            'action': 'edit',
        }
        return render(request, self.template_name, ctx)


class MediaTemplateItemDeleteView(MediaLeaderRequiredMixin, View):
    def post(self, request, pk):
        item = get_object_or_404(MediaPlanningTemplateItem.objects.select_related('template'), pk=pk)
        template_pk = item.template_id
        title = item.title
        item.delete()
        messages.success(request, f'Demanda "{title}" removida.')
        return redirect('media_template_detail', pk=template_pk)
