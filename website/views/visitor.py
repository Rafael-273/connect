import re
from django.views.generic import ListView
from django.views.generic.edit import CreateView
from django.db.models import Q
from django.utils.dateparse import parse_date
from django.utils.timezone import make_aware
from datetime import timedelta, datetime
from django.urls import reverse_lazy
from django.utils import timezone
from django.core.cache import cache
from django.http import HttpResponseForbidden
from ..models.visitor import Visitor
from ..forms.visitor import VisitorForm
from ..models.member import Member

RATE_LIMIT_MAX = 5
RATE_LIMIT_WINDOW = 60 * 60  # 1 hora


def _get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


class VisitorCreateView(CreateView):
    model = Visitor
    form_class = VisitorForm
    template_name = 'create/visitor.html'
    success_url = reverse_lazy('home')

    def form_valid(self, form):
        ip = _get_client_ip(self.request)
        cache_key = f'visitor_rate_{ip}'
        count = cache.get(cache_key, 0)
        if count >= RATE_LIMIT_MAX:
            return HttpResponseForbidden('Muitas submissões. Tente novamente mais tarde.')
        cache.set(cache_key, count + 1, RATE_LIMIT_WINDOW)
        form.instance.visit_date = timezone.now()
        return super().form_valid(form)


class VisitorListView(ListView):
    model = Visitor
    template_name = 'list/visitor.html'
    context_object_name = 'visitors'

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.order_by('-visit_date')

        query = self.request.GET.get('q', '')
        visit_period = self.request.GET.get('visit_period', 'all')

        if query:
            queryset = queryset.filter(
                Q(name__icontains=query) |
                Q(email__icontains=query) |
                Q(phone__icontains=query)
            )

        now = make_aware(datetime.now())

        if visit_period == '7_days':
            start_date = now - timedelta(days=7)
            queryset = queryset.filter(visit_date__gte=start_date)
        elif visit_period == '30_days':
            start_date = now - timedelta(days=30)
            queryset = queryset.filter(visit_date__gte=start_date)
        elif visit_period == 'all':
            pass  # sem filtro por data

        return queryset
    
    def normalize_phone_number(self, phone, default_ddd='21'):
        raw_phone = re.sub(r'\D', '', phone or '')

        if len(raw_phone) == 8 or len(raw_phone) == 9:
            return default_ddd + raw_phone
        elif len(raw_phone) == 10 or len(raw_phone) == 11:
            return raw_phone
        elif raw_phone.startswith('55') and len(raw_phone) >= 12:
            return raw_phone[2:]
        else:
            return raw_phone
        
    def clean_visitors(self, visitors):
        for visitor in visitors:
            visitor.cleaned_phone = self.normalize_phone_number(visitor.phone)
            if visitor.name:
                visitor.name = visitor.name.title()
                visitor.first_name = visitor.name.split()[0]
            else:
                visitor.first_name = ''
        return visitors

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        visitors = context['visitors']

        for visitor in visitors:
            visitor.cleaned_phone = self.normalize_phone_number(visitor.phone)

        context['visitors'] = self.clean_visitors(visitors)
        context['query'] = self.request.GET.get('q', '')
        context['visit_data'] = self.request.GET.get('visit_data', '')
        return context