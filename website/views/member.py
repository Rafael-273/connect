import uuid
from django.contrib.auth import get_user_model
from django.views.generic.edit import CreateView
from django.views.generic import ListView, DetailView
from django.urls import reverse_lazy
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from ..models.member import Member
from ..models.follow_up import FollowUp, FollowUpReport
from ..forms.member import MemberForm
from ..forms.follow_up import FollowUpReportForm
from ..models.user import User

User = get_user_model()


class MemberCreateView(CreateView):
    model = Member
    form_class = MemberForm
    template_name = 'create/member.html'
    success_url = reverse_lazy('home')

    def form_valid(self, form):
        email = self.request.POST.get('email')
        if not email:
            email = f"{uuid.uuid4().hex[:10]}@autogerado.com"

        user = User.objects.create_user(
            email=email,
            password='senha_padrão'
        )

        member = form.save(commit=False)
        member.user = user
        member.save()

        self.object = member

        return super().form_valid(form)
    

class NewConvertsListView(ListView):
    model = Member
    template_name = 'list/new_converts.html'
    context_object_name = 'members'

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.order_by('-created_at')
        query = self.request.GET.get('q', '')
        queryset = queryset.filter(conversion__in=['new_convert', 'reconciliation'])
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query) |
                Q(phone__icontains=query)
            )
        return queryset

    def normalize_phone_number(self, phone, default_ddd='21'):
        import re
        raw_phone = re.sub(r'\D', '', phone or '')
        if len(raw_phone) == 8 or len(raw_phone) == 9:
            return default_ddd + raw_phone
        elif len(raw_phone) == 10 or len(raw_phone) == 11:
            return raw_phone
        elif raw_phone.startswith('55') and len(raw_phone) >= 12:
            return raw_phone[2:]
        else:
            return raw_phone

    def clean_members(self, members):
        for member in members:
            member.cleaned_phone = self.normalize_phone_number(member.phone)
            if member.name:
                member.name = member.name.title()
                member.first_name = member.name.split()[0]
            else:
                member.first_name = ''
        return members

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        members = context['members']
        for member in members:
            member.cleaned_phone = self.normalize_phone_number(member.phone)
        context['members'] = self.clean_members(members)
        context['query'] = self.request.GET.get('q', '')
        return context


# ==================== VIEWS DE CONSOLIDAÇÃO ====================

@login_required
def member_consolidation_list(request):
    """Lista todos os consolidados do membro logado"""
    try:
        member = request.user.member
    except Member.DoesNotExist:
        messages.error(request, "Você precisa estar cadastrado como membro para acessar esta área.")
        return redirect('member_dashboard')
    
    # Buscar consolidações onde o membro é responsável
    followups = FollowUp.objects.filter(
        responsible=member,
        is_active=True
    ).select_related('accompanied', 'template').prefetch_related('reports').order_by('-created_at')
    
    context = {
        'followups': followups,
        'can_consolidate': member.is_available_to_consolidate,
    }
    
    return render(request, 'member/consolidation_list.html', context)


@login_required
def member_consolidation_detail(request, followup_id):
    """Detalhes de um consolidado específico - mostra dicas do período atual"""
    try:
        member = request.user.member
    except Member.DoesNotExist:
        messages.error(request, "Você precisa estar cadastrado como membro.")
        return redirect('member_dashboard')
    
    # Buscar o follow-up
    followup = get_object_or_404(
        FollowUp.objects.select_related('accompanied', 'responsible', 'template'),
        id=followup_id,
        responsible=member
    )
    
    # Buscar dicas do período atual
    current_step = followup.current_step
    
    # Buscar histórico de relatórios
    reports = followup.reports.all().order_by('-date')[:10]
    
    # Verificar se pode enviar relatório (se já não enviou para a semana atual)
    can_submit_report = not followup.reports.filter(week=followup.current_week).exists()
    
    # Nome do período sempre será "Semana"
    period_name = "Semana"
    
    context = {
        'followup': followup,
        'current_step': current_step,
        'reports': reports,
        'can_submit_report': can_submit_report,
        'period_name': period_name,
    }
    
    return render(request, 'member/consolidation_detail.html', context)


@login_required
def member_consolidation_report(request, followup_id):
    """Formulário para enviar relatório do período"""
    try:
        member = request.user.member
    except Member.DoesNotExist:
        messages.error(request, "Você precisa estar cadastrado como membro.")
        return redirect('member_dashboard')
    
    # Buscar o follow-up
    followup = get_object_or_404(
        FollowUp.objects.select_related('accompanied', 'template'),
        id=followup_id,
        responsible=member
    )
    
    # Verificar se já existe relatório para a semana atual
    if followup.reports.filter(week=followup.current_week).exists():
        messages.warning(request, "Você já enviou o relatório para esta semana.")
        return redirect('member_consolidation_detail', followup_id=followup.id)
    
    if request.method == 'POST':
        form = FollowUpReportForm(request.POST, followup=followup)
        if form.is_valid():
            report = form.save(commit=False)
            report.followup = followup
            report.save()
            
            messages.success(request, f"Relatório da semana {report.week} enviado com sucesso!")
            return redirect('member_consolidation_detail', followup_id=followup.id)
    else:
        form = FollowUpReportForm(followup=followup)
    
    # Buscar dicas da semana atual
    current_step = followup.current_step
    
    # Nome do período sempre será "Semana"
    period_name = "Semana"
    
    context = {
        'followup': followup,
        'form': form,
        'current_step': current_step,
        'period_name': period_name,
    }
    
    return render(request, 'member/consolidation_report.html', context)