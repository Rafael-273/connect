import re
from django.views.generic import ListView, TemplateView
from django.views.generic.edit import CreateView
from django.contrib.auth.mixins import LoginRequiredMixin
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


class PastoralVisitorListView(LoginRequiredMixin, ListView):
    """Visão de acompanhamento de visitantes exclusiva para pastores."""

    model = Visitor
    template_name = 'member/visitor_list.html'
    context_object_name = 'visitors'
    paginate_by = 10
    login_url = 'member_login'

    def dispatch(self, request, *args, **kwargs):
        self.member = getattr(request.user, 'member', None)
        if not self.member or self.member.church_role != 'pastor':
            messages.error(request, 'Esta área é exclusiva para pastores.')
            return redirect('member_dashboard')
        return super().dispatch(request, *args, **kwargs)

    @staticmethod
    def _normalize_phone(phone, default_ddd='21'):
        raw = re.sub(r'\D', '', phone or '')
        if len(raw) in (8, 9):
            return default_ddd + raw
        if len(raw) in (10, 11):
            return raw
        if raw.startswith('55') and len(raw) >= 12:
            return raw[2:]
        return raw

    def get_queryset(self):
        qs = Visitor.objects.select_related('neighborhood').order_by('-visit_date', '-id')
        query = self.request.GET.get('q', '').strip()
        period = self.request.GET.get('period', 'all')
        if query:
            qs = qs.filter(Q(name__icontains=query) | Q(phone__icontains=query) | Q(email__icontains=query))

        today = timezone.localdate()
        if period == '7_days':
            qs = qs.filter(visit_date__gte=today - timedelta(days=6))
        elif period == '30_days':
            qs = qs.filter(visit_date__gte=today - timedelta(days=29))
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        for visitor in context['visitors']:
            visitor.cleaned_phone = self._normalize_phone(visitor.phone)
            visitor.first_name = (visitor.name or '').split()[0] if visitor.name else ''

        context.update({
            'pastoral_view': True,
            'query': self.request.GET.get('q', ''),
            'period': self.request.GET.get('period', 'all'),
            'total': context['page_obj'].paginator.count,
        })
        return context


class PastoralVisitorReportView(LoginRequiredMixin, TemplateView):
    """Resumo de visitantes disponível exclusivamente para pastores."""

    template_name = 'member/pastoral_visitor_report.html'
    login_url = 'member_login'

    def dispatch(self, request, *args, **kwargs):
        member = getattr(request.user, 'member', None)
        if not member or member.church_role != 'pastor':
            messages.error(request, 'Esta área é exclusiva para pastores.')
            return redirect('member_dashboard')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        visitors = Visitor.objects.all()
        total = visitors.count()
        month_names = ('Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez')
        month_starts = []
        month_cursor = today.replace(day=1)
        for _ in range(6):
            month_starts.append(month_cursor)
            previous_month_last_day = month_cursor - timedelta(days=1)
            month_cursor = previous_month_last_day.replace(day=1)
        month_starts.reverse()

        monthly_visits = []
        for month_start in month_starts:
            next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
            monthly_visits.append({
                'label': month_names[month_start.month - 1],
                'count': visitors.filter(visit_date__gte=month_start, visit_date__lt=next_month).count(),
            })
        max_monthly_visits = max((month['count'] for month in monthly_visits), default=0)
        for month in monthly_visits:
            month['height'] = max(8, round((month['count'] / max_monthly_visits) * 100)) if max_monthly_visits else 8

        home_prayer_visitors = visitors.filter(
            wants_home_prayer=True
        ).order_by('-visit_date', '-id')[:5]

        context['report'] = {
            'total': total,
            'last_7_days': visitors.filter(visit_date__gte=today - timedelta(days=6)).count(),
            'this_month': visitors.filter(visit_date__year=today.year, visit_date__month=today.month).count(),
            'prayer_requests': visitors.exclude(prayer_request__isnull=True).exclude(prayer_request='').count(),
        }
        context['monthly_visits'] = monthly_visits
        context['home_prayer_visitors'] = home_prayer_visitors
        return context
