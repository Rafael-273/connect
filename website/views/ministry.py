from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.db.models import Q, Count

from website.models import Ministry, Member, MinistryMembership


class StaffRequiredMixin(UserPassesTestMixin):
    """Mixin para verificar se o usuário é staff"""
    def test_func(self):
        return self.request.user.is_staff or self.request.user.user_type == 'admin'
    
    def handle_no_permission(self):
        messages.error(self.request, 'Você não tem permissão para acessar esta página.')
        return redirect('admin_dashboard')


class MinistryListView(LoginRequiredMixin, StaffRequiredMixin, ListView):
    """Lista todos os ministérios"""
    model = Ministry
    template_name = 'admin_panel/ministries/list.html'
    context_object_name = 'ministries'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = Ministry.objects.annotate(
            total_members=Count('memberships', filter=Q(memberships__is_active=True)),
            total_leaders=Count('memberships', filter=Q(memberships__role='leader', memberships__is_active=True))
        )
        
        # Filtros
        search = self.request.GET.get('search')
        status = self.request.GET.get('status')
        
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(description__icontains=search)
            )
        
        if status == 'active':
            queryset = queryset.filter(is_active=True)
        elif status == 'inactive':
            queryset = queryset.filter(is_active=False)
        
        return queryset.order_by('name')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Ministérios'
        context['total_ministries'] = Ministry.objects.filter(is_active=True).count()
        return context


class MinistryCreateView(LoginRequiredMixin, StaffRequiredMixin, CreateView):
    """Criar novo ministério"""
    model = Ministry
    template_name = 'admin_panel/ministries/form.html'
    fields = ['name', 'description', 'color', 'is_active']
    success_url = reverse_lazy('ministry_list')
    
    def form_valid(self, form):
        messages.success(self.request, f'Ministério "{form.instance.name}" criado com sucesso!')
        return super().form_valid(form)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Criar Ministério'
        context['action'] = 'create'
        return context


class MinistryUpdateView(LoginRequiredMixin, StaffRequiredMixin, UpdateView):
    """Editar ministério"""
    model = Ministry
    template_name = 'admin_panel/ministries/form.html'
    fields = ['name', 'description', 'color', 'is_active']
    success_url = reverse_lazy('ministry_list')
    
    def form_valid(self, form):
        messages.success(self.request, f'Ministério "{form.instance.name}" atualizado com sucesso!')
        return super().form_valid(form)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Editar Ministério - {self.object.name}'
        context['action'] = 'edit'
        return context


class MinistryDeleteView(LoginRequiredMixin, StaffRequiredMixin, DeleteView):
    """Deletar ministério"""
    model = Ministry
    template_name = 'admin_panel/ministries/delete.html'
    success_url = reverse_lazy('ministry_list')
    
    def delete(self, request, *args, **kwargs):
        ministry_name = self.get_object().name
        messages.success(request, f'Ministério "{ministry_name}" deletado com sucesso!')
        return super().delete(request, *args, **kwargs)


class MinistryMembersView(LoginRequiredMixin, StaffRequiredMixin, ListView):
    """Gerenciar membros de um ministério específico"""
    model = MinistryMembership
    template_name = 'admin_panel/ministries/members.html'
    context_object_name = 'memberships'
    paginate_by = 50
    
    def get_queryset(self):
        self.ministry = get_object_or_404(Ministry, pk=self.kwargs['pk'])
        queryset = MinistryMembership.objects.filter(
            ministry=self.ministry
        ).select_related('member')
        
        # Filtros
        search = self.request.GET.get('search')
        role = self.request.GET.get('role')
        status = self.request.GET.get('status')
        
        if search:
            queryset = queryset.filter(
                Q(member__name__icontains=search) |
                Q(member__phone__icontains=search)
            )
        
        if role:
            queryset = queryset.filter(role=role)
        
        if status == 'active':
            queryset = queryset.filter(is_active=True)
        elif status == 'inactive':
            queryset = queryset.filter(is_active=False)
        
        return queryset.order_by('-role', 'member__name')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['ministry'] = self.ministry
        context['title'] = f'Membros - {self.ministry.name}'
        
        # Estatísticas
        context['total_members'] = self.ministry.get_member_count()
        context['total_leaders'] = self.ministry.get_leader_count()
        
        # Membros disponíveis para adicionar (não estão neste ministério)
        existing_member_ids = MinistryMembership.objects.filter(
            ministry=self.ministry
        ).values_list('member_id', flat=True)
        
        context['available_members'] = Member.objects.filter(
            is_active=True
        ).exclude(
            id__in=existing_member_ids
        ).order_by('name')
        
        return context


@login_required
def ministry_add_member(request, ministry_id, member_id):
    """Adiciona um membro ao ministério"""
    if not request.user.is_staff and request.user.user_type != 'admin':
        messages.error(request, 'Você não tem permissão para realizar esta ação.')
        return redirect('admin_dashboard')
    
    ministry = get_object_or_404(Ministry, id=ministry_id)
    member = get_object_or_404(Member, id=member_id)
    
    # Verificar se já existe
    if MinistryMembership.objects.filter(ministry=ministry, member=member).exists():
        messages.warning(request, f'{member.name} já faz parte do ministério {ministry.name}!')
    else:
        # Criar membership
        MinistryMembership.objects.create(
            ministry=ministry,
            member=member,
            role='member'
        )
        messages.success(request, f'{member.name} adicionado(a) ao ministério {ministry.name}!')
    
    return redirect('ministry_members', pk=ministry_id)


@login_required
def ministry_remove_member(request, ministry_id, member_id):
    """Remove um membro do ministério"""
    if not request.user.is_staff and request.user.user_type != 'admin':
        messages.error(request, 'Você não tem permissão para realizar esta ação.')
        return redirect('admin_dashboard')
    
    ministry = get_object_or_404(Ministry, id=ministry_id)
    member = get_object_or_404(Member, id=member_id)
    
    # Buscar e deletar membership
    membership = MinistryMembership.objects.filter(
        ministry=ministry,
        member=member
    ).first()
    
    if membership:
        membership.delete()
        messages.success(request, f'{member.name} removido(a) do ministério {ministry.name}!')
    else:
        messages.warning(request, f'{member.name} não faz parte do ministério {ministry.name}!')
    
    return redirect('ministry_members', pk=ministry_id)


@login_required
def ministry_toggle_role(request, ministry_id, member_id):
    """Alterna o papel do membro entre líder e membro"""
    if not request.user.is_staff and request.user.user_type != 'admin':
        messages.error(request, 'Você não tem permissão para realizar esta ação.')
        return redirect('admin_dashboard')
    
    ministry = get_object_or_404(Ministry, id=ministry_id)
    member = get_object_or_404(Member, id=member_id)
    
    membership = get_object_or_404(
        MinistryMembership,
        ministry=ministry,
        member=member
    )
    
    # Alternar papel
    if membership.role == 'leader':
        membership.role = 'member'
        messages.success(request, f'{member.name} não é mais líder do ministério {ministry.name}.')
    else:
        membership.role = 'leader'
        messages.success(request, f'{member.name} agora é líder do ministério {ministry.name}!')
    
    membership.save()
    
    return redirect('ministry_members', pk=ministry_id)


@login_required
def ministry_toggle_status(request, ministry_id, member_id):
    """Ativa/desativa um membro no ministério"""
    if not request.user.is_staff and request.user.user_type != 'admin':
        messages.error(request, 'Você não tem permissão para realizar esta ação.')
        return redirect('admin_dashboard')
    
    ministry = get_object_or_404(Ministry, id=ministry_id)
    member = get_object_or_404(Member, id=member_id)
    
    membership = get_object_or_404(
        MinistryMembership,
        ministry=ministry,
        member=member
    )
    
    # Alternar status
    membership.is_active = not membership.is_active
    membership.save()
    
    status_text = 'ativo' if membership.is_active else 'inativo'
    messages.success(request, f'{member.name} está agora {status_text} no ministério {ministry.name}.')
    
    return redirect('ministry_members', pk=ministry_id)
