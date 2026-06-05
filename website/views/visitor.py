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
from django.http import HttpResponseForbidden, HttpResponse
from django.contrib import messages
from ..models.visitor import Visitor
from ..forms.visitor import VisitorForm
from ..models.member import Member
from ..models.ministry_membership import MinistryMembership
from .mixins import MemberRequiredMixin

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


class MemberVisitorCreateView(MemberRequiredMixin, CreateView):
    model = Visitor
    form_class = VisitorForm
    template_name = 'member/visitor_create.html'
    success_url = reverse_lazy('member_dashboard')

    def form_valid(self, form):
        form.instance.visit_date = timezone.now()
        response = super().form_valid(form)
        messages.success(self.request, 'Visitante cadastrado com sucesso!')
        return response


class MemberVisitorListView(MemberRequiredMixin, ListView):
    model = Visitor
    template_name = 'member/visitor_list.html'
    context_object_name = 'visitors'
    paginate_by = 10

    def dispatch(self, request, *args, **kwargs):
        member = getattr(self, 'member', None)
        if member is None:
            # MemberRequiredMixin sets self.member in get_member; call super first
            response = super().dispatch(request, *args, **kwargs)
            return response
        is_boas_vindas = MinistryMembership.objects.filter(
            member=self.member, is_active=True, ministry__name__icontains='boas vindas'
        ).exists()
        if not is_boas_vindas:
            from django.shortcuts import redirect
            return redirect('member_dashboard')
        return super().dispatch(request, *args, **kwargs)

    def _normalize_phone(self, phone, default_ddd='21'):
        raw = re.sub(r'\D', '', phone or '')
        if len(raw) in (8, 9):
            return default_ddd + raw
        if len(raw) in (10, 11):
            return raw
        if raw.startswith('55') and len(raw) >= 12:
            return raw[2:]
        return raw

    def get_queryset(self):
        qs = Visitor.objects.order_by('-visit_date')
        q = self.request.GET.get('q', '').strip()
        period = self.request.GET.get('period', 'all')
        if q:
            qs = qs.filter(
                Q(name__icontains=q) | Q(phone__icontains=q)
            )
        now = make_aware(datetime.now())
        if period == '7_days':
            qs = qs.filter(visit_date__gte=now - timedelta(days=7))
        elif period == '30_days':
            qs = qs.filter(visit_date__gte=now - timedelta(days=30))
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        visitors = context['visitors']
        for v in visitors:
            v.cleaned_phone = self._normalize_phone(v.phone)
            v.first_name = (v.name or '').split()[0] if v.name else ''
        context['query'] = self.request.GET.get('q', '')
        context['period'] = self.request.GET.get('period', 'all')
        page_obj = context.get('page_obj')
        if page_obj:
            context['total'] = page_obj.paginator.count
        else:
            context['total'] = len(visitors)
        return context