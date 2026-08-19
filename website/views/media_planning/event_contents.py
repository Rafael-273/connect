from django.shortcuts import redirect
from django.urls import reverse
from django.views import View

from .mixins import MediaMemberRequiredMixin


class MediaEventContentsView(MediaMemberRequiredMixin, View):
    """Redireciona para o hub Demandas e Eventos com o evento selecionado."""

    def get(self, request, event_pk):
        return redirect(f"{reverse('media_content_list')}?selected=event-{event_pk}")
