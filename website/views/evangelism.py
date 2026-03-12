from django.views.generic.edit import CreateView
from django.views.generic import ListView
from django.urls import reverse_lazy
from django.utils import timezone
from django.db.models import Q
from django.core.cache import cache
from django.http import HttpResponseForbidden
from ..models.evangelism import Evangelized
from ..forms.evangelism import EvangelizedForm

RATE_LIMIT_MAX = 5
RATE_LIMIT_WINDOW = 60 * 60  # 1 hora


def _get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


class EvangelismCreateView(CreateView):
    model = Evangelized
    form_class = EvangelizedForm
    template_name = 'create/evangelism.html'
    success_url = reverse_lazy('evangelism_list')

    def form_valid(self, form):
        ip = _get_client_ip(self.request)
        cache_key = f'evangelism_rate_{ip}'
        count = cache.get(cache_key, 0)
        if count >= RATE_LIMIT_MAX:
            return HttpResponseForbidden('Muitas submissões. Tente novamente mais tarde.')
        cache.set(cache_key, count + 1, RATE_LIMIT_WINDOW)
        form.instance.evangelism_date = timezone.now()
        return super().form_valid(form)


class EvangelismListView(ListView):
    model = Evangelized
    template_name = 'list/evangelism.html'
    context_object_name = 'evangelisms'
    ordering = ['-evangelism_date', '-id']

    def get_queryset(self):
        queryset = super().get_queryset().select_related('neighborhood')
        query = self.request.GET.get('q', '')
        period = self.request.GET.get('period', '')

        if query:
            queryset = queryset.filter(
                Q(name__icontains=query) |
                Q(phone__icontains=query) |
                Q(address__icontains=query)
            )

        if period == '7_days':
            from datetime import timedelta
            queryset = queryset.filter(evangelism_date__gte=timezone.now().date() - timedelta(days=7))
        elif period == '30_days':
            from datetime import timedelta
            queryset = queryset.filter(evangelism_date__gte=timezone.now().date() - timedelta(days=30))

        return queryset

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['query'] = self.request.GET.get('q', '')
        ctx['period'] = self.request.GET.get('period', '')
        return ctx
