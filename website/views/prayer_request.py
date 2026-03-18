from django.views.generic.edit import CreateView
from django.views.generic import ListView
from django.urls import reverse_lazy
from django.core.cache import cache
from django.http import HttpResponse
from ..models.prayer_request import PrayerRequest
from ..forms.prayer_request import PrayerRequestForm

RATE_LIMIT_MAX = 3
RATE_LIMIT_WINDOW = 60 * 60  # 1 hour in seconds


def _get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


class PrayerRequestCreateView(CreateView):
    model = PrayerRequest
    form_class = PrayerRequestForm
    template_name = 'create/prayer_request.html'
    success_url = reverse_lazy('prayer_request_create')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['submitted'] = self.request.GET.get('submitted') == '1'
        return context

    def form_valid(self, form):
        ip = _get_client_ip(self.request)
        cache_key = f'prayer_request_rate_{ip}'
        count = cache.get(cache_key, 0)

        if count >= RATE_LIMIT_MAX:
            return HttpResponse(
                'Muitas submissões. Tente novamente mais tarde.', status=429
            )

        cache.set(cache_key, count + 1, RATE_LIMIT_WINDOW)
        response = super().form_valid(form)
        return response

    def get_success_url(self):
        return reverse_lazy('prayer_request_create') + '?submitted=1'


class PrayerRequestListView(ListView):
    model = PrayerRequest
    template_name = 'list/prayer_request.html'
    context_object_name = 'prayer_requests'

    def get_queryset(self):
        return PrayerRequest.objects.order_by('-created_at')
