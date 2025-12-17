import uuid
from datetime import date
from django.contrib.auth import get_user_model
from django.views.generic.edit import CreateView
from django.views.generic import ListView, DetailView
from django.urls import reverse_lazy
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.core.files.base import ContentFile
import base64
from ..models.member import Member
from ..models.follow_up import FollowUp, FollowUpReport, FollowUpTemplate
from ..models.ministry_membership import MinistryMembership
from ..forms.member import MemberForm
from ..forms.follow_up import FollowUpReportForm
from ..models.user import User

User = get_user_model()


class MemberCreateView(CreateView):
    model = Member
    form_class = MemberForm
    template_name = 'create/member.html'
    success_url = reverse_lazy('member_login')

    def form_valid(self, form):
        email = form.cleaned_data.get('email')
        password = form.cleaned_data.get('password')
        
        # Validar se o email já existe
        if email and User.objects.filter(email=email).exists():
            messages.error(self.request, 'Este e-mail já está cadastrado no sistema. Por favor, use outro e-mail.')
            return self.form_invalid(form)
        
        if not email:
            messages.error(self.request, 'O e-mail é obrigatório.')
            return self.form_invalid(form)

        try:
            # Criar usuário com a senha fornecida
            user = User.objects.create_user(
                email=email,
                password=password
            )

            member = form.save(commit=False)
            member.user = user
            
            # Processar imagem cropada em base64
            cropped_image_data = self.request.POST.get('cropped_image_data')
            if cropped_image_data:
                # Remove o prefixo "data:image/jpeg;base64," se existir
                if ',' in cropped_image_data:
                    format, imgstr = cropped_image_data.split(';base64,')
                    ext = format.split('/')[-1]
                else:
                    imgstr = cropped_image_data
                    ext = 'jpg'
                
                # Decodifica a imagem base64
                data = ContentFile(base64.b64decode(imgstr), name=f'profile_{user.id}.{ext}')
                member.profile_picture = data
            
            member.save()

            self.object = member
            messages.success(self.request, 'Cadastro realizado com sucesso! Você já pode fazer login.')
            return super().form_valid(form)
            
        except Exception as e:
            messages.error(self.request, f'Erro ao realizar cadastro: {str(e)}')
            return self.form_invalid(form)
    

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
    
    # Verifica se o membro está no ministério de ministração (sistema antigo e novo)
    is_ministration_old = member.ministry.filter(name__icontains='ministração').exists()
    is_ministration_new = MinistryMembership.objects.filter(
        member=member,
        ministry__name__icontains='ministração',
        is_active=True
    ).exists()
    is_ministration_member = is_ministration_old or is_ministration_new
    
    context = {
        'followups': followups,
        'can_consolidate': member.is_available_to_consolidate,
        'is_ministration_member': is_ministration_member,
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
    
    # Verifica se o membro está no ministério de ministração (sistema antigo e novo)
    is_ministration_old = member.ministry.filter(name__icontains='ministração').exists()
    is_ministration_new = MinistryMembership.objects.filter(
        member=member,
        ministry__name__icontains='ministração',
        is_active=True
    ).exists()
    is_ministration_member = is_ministration_old or is_ministration_new
    
    context = {
        'followup': followup,
        'current_step': current_step,
        'reports': reports,
        'can_submit_report': can_submit_report,
        'period_name': period_name,
        'is_ministration_member': is_ministration_member,
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
    
    # Verifica se o membro está no ministério de ministração (sistema antigo e novo)
    is_ministration_old = member.ministry.filter(name__icontains='ministração').exists()
    is_ministration_new = MinistryMembership.objects.filter(
        member=member,
        ministry__name__icontains='ministração',
        is_active=True
    ).exists()
    is_ministration_member = is_ministration_old or is_ministration_new
    
    context = {
        'followup': followup,
        'form': form,
        'current_step': current_step,
        'period_name': period_name,
        'is_ministration_member': is_ministration_member,
    }
    
    return render(request, 'member/consolidation_report.html', context)


@login_required
def consolidator_guide(request):
    """Página com guia e materiais para consolidadores"""
    try:
        member = request.user.member
    except Member.DoesNotExist:
        messages.error(request, "Você precisa estar cadastrado como membro.")
        return redirect('member_dashboard')
    
    # Verifica se o membro está no ministério de ministração (sistema antigo e novo)
    is_ministration_old = member.ministry.filter(name__icontains='ministração').exists()
    is_ministration_new = MinistryMembership.objects.filter(
        member=member,
        ministry__name__icontains='ministração',
        is_active=True
    ).exists()
    is_ministration_member = is_ministration_old or is_ministration_new
    
    context = {
        'member': member,
        'is_ministration_member': is_ministration_member,
    }
    
    return render(request, 'member/consolidator_guide.html', context)


@login_required
def consolidator_assignments(request):
    """Página onde consolidadores escolhem quem consolidar"""
    try:
        member = request.user.member
    except Member.DoesNotExist:
        messages.error(request, "Você precisa estar cadastrado como membro.")
        return redirect('member_dashboard')
    
    # Buscar consolidações ativas do membro
    my_consolidations = FollowUp.objects.filter(
        responsible=member,
        is_active=True
    ).select_related('accompanied').count()
    
    # Calcular idade do consolidador
    from datetime import date
    consolidator_age = None
    if member.birth_date:
        today = date.today()
        consolidator_age = today.year - member.birth_date.year - (
            (today.month, today.day) < (member.birth_date.month, member.birth_date.day)
        )
    
    # Filtros base: tem conversão, não está consolidado, não tem consolidação ativa
    available_people = Member.objects.filter(
        conversion__in=['new_convert', 'reconciled'],  # Tem conversão
        is_consolidated=False,  # Não está consolidado
        is_active=True
    ).exclude(
        id=member.id  # Não é o próprio consolidador
    )
    
    # Excluir pessoas que já têm consolidação ativa
    # Fazemos isso separadamente para evitar problemas com distinct()
    people_with_active_followup = FollowUp.objects.filter(
        is_active=True
    ).values_list('accompanied_id', flat=True)
    
    available_people = available_people.exclude(
        id__in=people_with_active_followup
    )
    
    # Filtro de gênero: mesmo sexo
    if member.gender:
        available_people = available_people.filter(gender=member.gender)
    
    # Filtro de idade se o consolidador tem data de nascimento
    if consolidator_age:
        if consolidator_age >= 50:
            # 50+ pode consolidar todos de 40 anos pra cima
            available_people = available_people.filter(
                birth_date__lte=date(today.year - 40, today.month, today.day)
            )
        else:
            # Diferença de 10 anos para mais ou menos
            min_birth_year = today.year - (consolidator_age + 10)
            max_birth_year = today.year - (consolidator_age - 10)
            
            available_people = available_people.filter(
                birth_date__year__gte=min_birth_year,
                birth_date__year__lte=max_birth_year
            )
    
    # Ordenar por data de conversão mais recente e aplicar distinct
    available_people = available_people.distinct().order_by('-conversion_date', '-created_at')
    
    # Verifica se o membro está no ministério de ministração (sistema antigo e novo)
    is_ministration_old = member.ministry.filter(name__icontains='ministração').exists()
    is_ministration_new = MinistryMembership.objects.filter(
        member=member,
        ministry__name__icontains='ministração',
        is_active=True
    ).exists()
    is_ministration_member = is_ministration_old or is_ministration_new
    
    context = {
        'member': member,
        'available_people': available_people,
        'my_consolidations_count': my_consolidations,
        'can_consolidate': member.is_available_to_consolidate,
        'consolidator_age': consolidator_age,
        'is_ministration_member': is_ministration_member,
    }
    
    return render(request, 'member/consolidator_assignments.html', context)


@login_required
def request_consolidation(request, person_id):
    """Criar uma nova consolidação automaticamente"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido'}, status=405)
    
    # Obter o membro logado
    member = get_object_or_404(Member, user=request.user)
    
    # Verificar se pode consolidar
    if not member.is_available_to_consolidate:
        return JsonResponse({
            'error': 'Você não está disponível para consolidar no momento.'
        }, status=403)
    
    # Obter a pessoa a ser consolidada
    person = get_object_or_404(Member, id=person_id)
    
    # Verificar se a pessoa já tem uma consolidação ativa
    if FollowUp.objects.filter(accompanied=person, is_active=True).exists():
        return JsonResponse({
            'error': 'Esta pessoa já está sendo consolidada.'
        }, status=400)
    
    # Buscar o último template de consolidação criado
    last_template = FollowUpTemplate.objects.order_by('-created_at').first()
    
    # Criar a consolidação
    followup = FollowUp.objects.create(
        responsible=member,
        accompanied=person,
        template=last_template,  # Adiciona o último template automaticamente
        is_active=True,
        profile_notes=f'Consolidação iniciada em {date.today().strftime("%d/%m/%Y")}'
    )
    
    return JsonResponse({
        'success': True,
        'message': 'Consolidação iniciada com sucesso!',
        'followup_id': followup.id
    })