from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.db.models import Count, Q
from django.views import View

from ...forms.media_organization import MediaTemplateUnifiedForm
from ...models.media_event_type import MediaEventType
from .mixins import MediaLeaderRequiredMixin, MediaMemberRequiredMixin


def event_type_list_context(request, create_form=None):
    """Contexto compartilhado entre a listagem e o modal de criação."""
    search = request.GET.get('search', '').strip()
    event_types = MediaEventType.objects.select_related('planning_template').annotate(
        template_item_count=Count('planning_template__items', distinct=True),
        event_count=Count('events', distinct=True),
    )
    if search:
        event_types = event_types.filter(
            Q(name__icontains=search) | Q(description__icontains=search)
        )
    return {
        'event_types': event_types,
        'search': search,
        'create_form': create_form or MediaTemplateUnifiedForm(),
        'show_create_modal': request.GET.get('create') == '1' or create_form is not None,
    }


class MediaEventTypeListView(MediaMemberRequiredMixin, View):
    def get(self, request):
        return render(request, 'member/media_planning/event_type_list.html', {
            **self._nav_context(),
            **event_type_list_context(request),
        })


class MediaEventTypeCreateView(MediaLeaderRequiredMixin, View):
    def get(self, request):
        return redirect('media_template_create')


class MediaEventTypeUpdateView(MediaLeaderRequiredMixin, View):
    def get(self, request, pk):
        from ...models.media_event_type import MediaPlanningTemplate
        try:
            tpl = MediaPlanningTemplate.objects.get(event_type_id=pk)
            return redirect('media_template_edit', pk=tpl.pk)
        except MediaPlanningTemplate.DoesNotExist:
            return redirect('media_template_setup', event_type_pk=pk)

    def post(self, request, pk):
        return self.get(request, pk)


class MediaEventTypeDeleteView(MediaLeaderRequiredMixin, View):
    """Remove um tipo e seu template; eventos vinculados ficam sem tipo."""

    def post(self, request, pk):
        event_type = get_object_or_404(MediaEventType, pk=pk)
        name = event_type.name
        demand_count = getattr(event_type.planning_template, 'items', None)
        demand_count = demand_count.count() if demand_count else 0
        event_count = event_type.events.count()
        event_type.delete()

        detail = f' e {demand_count} demanda(s) padrão'
        if event_count:
            detail += f'; {event_count} evento(s) ficaram sem tipo'
        messages.success(request, f'Tipo "{name}" removido{detail}.')
        return redirect('media_event_type_list')
