from django.shortcuts import redirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View

from ...models.event import Event

from .mixins import MediaMemberRequiredMixin


class MediaEventContentsView(MediaMemberRequiredMixin, View):
    """Redireciona para o hub Demandas e Eventos com o evento selecionado."""

    def get(self, request, event_pk):
        get_object_or_404(Event, pk=event_pk, is_recurring=False)
        return redirect(f"{reverse('media_content_list')}?selected=event-{event_pk}")
