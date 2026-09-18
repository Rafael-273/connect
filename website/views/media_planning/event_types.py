from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.db.models import Count, Q
from django.views import View
from safedelete.models import HARD_DELETE

from ...forms.media_organization import MediaTemplateUnifiedForm
from ...models.media_event_type import MediaEventType
from .mixins import MediaLeaderRequiredMixin, MediaMemberRequiredMixin


def event_type_list_context(request, create_form=None):
    """Contexto compartilhado entre a listagem e o modal de criação."""
    search = request.GET.get('search', '').strip()
    event_types = MediaEventType.objects.select_related('planning_template').annotate(
        template_item_count=Count('planning_template__items', filter=Q(planning_template__items__deleted__isnull=True), distinct=True),
        event_count=Count('events', filter=Q(events__deleted__isnull=True), distinct=True),
    )
    if search:
        event_types = event_types.filter(
            Q(name__icontains=search) | Q(description__icontains=search)
        )
    return {
        'event_types': event_types.order_by('sort_order', 'name'),
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


class MediaEventTypeDeleteView(MediaMemberRequiredMixin, View):
    """Remove o tipo de evento, template padrão e demandas sugeridas."""

    def post(self, request, pk):
        event_type = get_object_or_404(MediaEventType, pk=pk)
        name = event_type.name
        event_type.events.update(event_type=None)
        event_type.delete(force_policy=HARD_DELETE)
        messages.success(request, f'Tipo "{name}" excluído.')
        return redirect('media_event_type_list')
