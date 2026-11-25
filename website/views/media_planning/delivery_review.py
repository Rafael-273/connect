from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from website.models import MediaTask
from website.services.content_deliveries import requirements_for
from website.services.delivery_reviews import decide_review, review_token
from .mixins import MediaLeaderRequiredMixin


class MediaDeliveryReviewView(MediaLeaderRequiredMixin, View):
    def get_task(self, pk):
        return get_object_or_404(MediaTask.objects.select_related('content__event', 'assigned_to__member'), pk=pk, content__deleted__isnull=True)

    def context(self, task, **extra):
        requirements = list(requirements_for(task))
        items = []
        for requirement in requirements:
            delivery = next(iter(requirement.deliveries.all()), None)
            items.append({'requirement': requirement,
                          'delivery': delivery if delivery and delivery.status != 'withdrawn' else None})
        return {
            **self._nav_context(), 'task': task,
            'review_items': items,
            'review_token': review_token(task), **extra,
        }

    def get(self, request, pk):
        task = self.get_task(pk)
        template = 'member/media_planning/partials/delivery_review_content.html' if request.GET.get('fragment') == '1' else 'member/media_planning/delivery_review.html'
        return render(request, template, self.context(task))

    def post(self, request, pk):
        task = self.get_task(pk)
        ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        try:
            task = decide_review(task.pk, request.user, request.POST.get('requirement_id'),
                request.POST.get('decision', ''), request.POST.get('note', ''), request.POST.get('review_token', ''))
        except ValidationError as exc:
            error = ' '.join(exc.messages)
            if ajax:
                return JsonResponse({'error': error}, status=400)
            task = self.get_task(pk)
            return render(request, 'member/media_planning/delivery_review.html', self.context(task,
                review_error=error, review_note=request.POST.get('note', ''),
                failed_requirement=request.POST.get('requirement_id', '')), status=400)
        if ajax:
            return JsonResponse({'status': task.status})
        messages.success(request, 'Avaliação registrada.')
        return redirect('media_delivery_review', pk=task.pk)
