from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib import messages
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.db.models import Q, Count
from datetime import datetime, timedelta

from website.models import WordOfKnowledge, Healing, Member, Ministry
from website.models.ministry_membership import MinistryMembership
from website.forms.word_of_knowledge import WordOfKnowledgeForm, HealingForm


class StaffRequiredMixin(UserPassesTestMixin):
    """Mixin para verificar se o usuário é staff"""
    def test_func(self):
        return self.request.user.is_staff or self.request.user.user_type == 'admin'
    
    def handle_no_permission(self):
        messages.error(self.request, 'Você não tem permissão para acessar esta página.')
        return redirect('admin_dashboard')


class MinistrationWordsListView(LoginRequiredMixin, StaffRequiredMixin, ListView):
    """Lista todas as palavras de conhecimento registradas"""
    model = WordOfKnowledge
    template_name = 'admin_panel/ministration/words_list.html'
    context_object_name = 'words'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = WordOfKnowledge.objects.select_related('member').prefetch_related('healings')
        
        # Filtros
        search = self.request.GET.get('search')
        service_type = self.request.GET.get('service_type')
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        
        if search:
            queryset = queryset.filter(
                Q(description__icontains=search) |
                Q(member__name__icontains=search)
            )
        
        if service_type:
            queryset = queryset.filter(service_type=service_type)
        
        if date_from:
            queryset = queryset.filter(service_date__gte=date_from)
        
        if date_to:
            queryset = queryset.filter(service_date__lte=date_to)
        
        return queryset.order_by('-service_date', '-recorded_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Palavras de Conhecimento'
        context['total_words'] = self.get_queryset().count()
        context['words_with_healing'] = self.get_queryset().filter(resulted_in_healing=True).count()
        return context


class MinistrationHealingsListView(LoginRequiredMixin, StaffRequiredMixin, ListView):
    """Lista todas as curas registradas"""
    model = Healing
    template_name = 'admin_panel/ministration/healings_list.html'
    context_object_name = 'healings'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = Healing.objects.select_related('member', 'word_of_knowledge')
        
        # Filtros
        search = self.request.GET.get('search')
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        body_part = self.request.GET.get('body_part')
        
        if search:
            queryset = queryset.filter(
                Q(healed_person_name__icontains=search) |
                Q(condition__icontains=search) |
                Q(description__icontains=search) |
                Q(member__name__icontains=search)
            )
        
        if date_from:
            queryset = queryset.filter(healing_date__gte=date_from)
        
        if date_to:
            queryset = queryset.filter(healing_date__lte=date_to)
        
        if body_part:
            queryset = queryset.filter(body_part__icontains=body_part)
        
        return queryset.order_by('-healing_date', '-recorded_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Curas Registradas'
        context['total_healings'] = self.get_queryset().count()
        return context


class MinistrationMembersListView(LoginRequiredMixin, StaffRequiredMixin, ListView):
    """Lista membros do ministério de ministração"""
    model = Member
    template_name = 'admin_panel/ministration/members_list.html'
    context_object_name = 'members'
    paginate_by = 50
    
    def get_queryset(self):
        # Buscar ministério de ministração
        ministry = Ministry.objects.filter(name__icontains='ministração').first()
        
        if ministry:
            # Membros do sistema antigo
            member_ids_old = set(Member.objects.filter(ministry=ministry).values_list('id', flat=True))
            
            # Membros do sistema novo
            member_ids_new = set(MinistryMembership.objects.filter(
                ministry=ministry,
                is_active=True
            ).values_list('member_id', flat=True))
            
            # Combinar ambos
            all_member_ids = member_ids_old | member_ids_new
            queryset = Member.objects.filter(id__in=all_member_ids)
        else:
            queryset = Member.objects.none()
        
        # Filtros
        search = self.request.GET.get('search')
        status = self.request.GET.get('status')
        
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(email__icontains=search) |
                Q(phone__icontains=search)
            )
        
        if status == 'active':
            queryset = queryset.filter(is_active=True)
        elif status == 'inactive':
            queryset = queryset.filter(is_active=False)
        
        return queryset.distinct().order_by('name')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Membros do Ministério'
        
        # Estatísticas
        ministry = Ministry.objects.filter(name__icontains='ministração').first()
        if ministry:
            context['ministry'] = ministry
            
            # Contar membros do sistema antigo
            members_old = set(Member.objects.filter(ministry=ministry).values_list('id', flat=True))
            
            # Contar membros do sistema novo
            members_new = set(MinistryMembership.objects.filter(
                ministry=ministry,
                is_active=True
            ).values_list('member_id', flat=True))
            
            # Total único
            all_member_ids = members_old | members_new
            context['total_members'] = len(all_member_ids)
            
            # Ativos
            active_member_ids = Member.objects.filter(
                id__in=all_member_ids,
                is_active=True
            ).values_list('id', flat=True)
            context['active_members'] = len(active_member_ids)
        else:
            context['ministry'] = None
            context['total_members'] = 0
            context['active_members'] = 0
        
        # Todos os membros ativos que NÃO estão no ministério (para adicionar)
        if ministry:
            context['available_members'] = Member.objects.filter(is_active=True).exclude(
                ministry=ministry
            ).order_by('name')
        else:
            context['available_members'] = Member.objects.filter(is_active=True).order_by('name')
        
        return context


def ministration_add_member(request, member_id):
    """Adiciona um membro ao ministério de ministração"""
    if not request.user.is_staff and request.user.user_type != 'admin':
        messages.error(request, 'Você não tem permissão para realizar esta ação.')
        return redirect('admin_dashboard')
    
    member = get_object_or_404(Member, id=member_id)
    ministry = Ministry.objects.filter(name__icontains='ministração').first()
    
    if not ministry:
        # Criar ministério se não existir
        ministry = Ministry.objects.create(
            name='Ministração',
            description='Ministério de Ministração - Palavras de Conhecimento e Cura'
        )
        messages.success(request, f'Ministério de Ministração criado com sucesso!')
    
    # Adicionar membro ao ministério (ManyToMany)
    member.ministry.add(ministry)
    
    messages.success(request, f'{member.name} adicionado(a) ao Ministério de Ministração!')
    return redirect('ministration_members_list')


def ministration_remove_member(request, member_id):
    """Remove um membro do ministério de ministração"""
    if not request.user.is_staff and request.user.user_type != 'admin':
        messages.error(request, 'Você não tem permissão para realizar esta ação.')
        return redirect('admin_dashboard')
    
    member = get_object_or_404(Member, id=member_id)
    ministry = Ministry.objects.filter(name__icontains='ministração').first()
    
    if ministry:
        # Remover membro do ministério (ManyToMany)
        member.ministry.remove(ministry)
    
    messages.success(request, f'{member.name} removido(a) do Ministério de Ministração!')
    return redirect('ministration_members_list')


def ministration_toggle_approver(request, member_id):
    """Alterna o status de aprovador de um membro do ministério"""
    if not request.user.is_staff and request.user.user_type != 'admin':
        messages.error(request, 'Você não tem permissão para realizar esta ação.')
        return redirect('admin_dashboard')
    
    member = get_object_or_404(Member, id=member_id)
    
    # Alternar status de aprovador
    member.is_approver = not member.is_approver
    member.save()
    
    if member.is_approver:
        messages.success(request, f'{member.name} agora é um aprovador de palavras de conhecimento!')
    else:
        messages.success(request, f'{member.name} não é mais um aprovador de palavras de conhecimento.')
    
    return redirect('ministration_members_list')

