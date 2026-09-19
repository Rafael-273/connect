from django.http import Http404
from django.shortcuts import render
from django.views import View

from ...models.media_content import CONTENT_TYPE_CHOICES, MediaContent
from .mixins import MediaMemberRequiredMixin


class MediaCategoryContentsView(MediaMemberRequiredMixin, View):
    template_name = 'member/media_planning/category_contents.html'

    def get(self, request, content_type):
        valid_types = [c[0] for c in CONTENT_TYPE_CHOICES]
        if content_type not in valid_types:
            raise Http404

        contents = (
            MediaContent.objects.filter(event__isnull=True, content_type=content_type)
            .select_related('responsible__member')
            .order_by('due_date', '-created_at')
        )

        content_type_label = dict(CONTENT_TYPE_CHOICES).get(content_type, content_type)

        ctx = {
            **self._nav_context(),
            'content_type': content_type,
            'content_type_label': content_type_label,
            'contents': contents,
        }
        return render(request, self.template_name, ctx)
