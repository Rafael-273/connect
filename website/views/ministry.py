from django.shortcuts import redirect, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.views import View
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.db.models import Q, Count
from safedelete.models import HARD_DELETE

from ..models import Ministry, MinistryMembership, Member
from .mixins import StaffRequiredMixin


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
        
        # Buscar memberships do novo sistema
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
        
        # Pegar memberships do novo sistema
        new_memberships = list(context['memberships'])
        
        # Pegar membros do sistema antigo que não estão no novo
        old_members = self.ministry.member_set.filter(is_active=True)
        existing_member_ids = set(m.member.id for m in new_memberships)
        
        # Criar "fake memberships" para membros do sistema antigo
        from collections import namedtuple
        FakeMembership = namedtuple('FakeMembership', ['member', 'role', 'is_active'])
        
        for member in old_members:
            if member.id not in existing_member_ids:
                new_memberships.append(FakeMembership(
                    member=member,
                    role='member',
                    is_active=True
                ))
        
        context['memberships'] = new_memberships
        
        # Estatísticas - contar ambos os sistemas
        total_new = MinistryMembership.objects.filter(
            ministry=self.ministry,
            is_active=True
        ).count()
        total_old = self.ministry.member_set.filter(is_active=True).count()
        context['total_members'] = max(total_new, total_old)
        
        context['total_leaders'] = MinistryMembership.objects.filter(
            ministry=self.ministry,
            role='leader',
            is_active=True
        ).count()
        
        # Membros disponíveis para adicionar (não estão em nenhum dos sistemas)
        existing_member_ids_new = MinistryMembership.objects.filter(
            ministry=self.ministry
        ).values_list('member_id', flat=True)
        
        existing_member_ids_old = self.ministry.member_set.values_list('id', flat=True)
        
        all_existing_ids = set(existing_member_ids_new) | set(existing_member_ids_old)
        
        context['available_members'] = Member.objects.filter(
            is_active=True
        ).exclude(
            id__in=all_existing_ids
        ).order_by('name')
        
        return context


class MinistryAddMemberView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Adiciona um membro ao ministério"""

    def post(self, request, ministry_id, member_id):
        ministry = get_object_or_404(Ministry, id=ministry_id)
        member = get_object_or_404(Member, id=member_id)

        try:
            existing_deleted = MinistryMembership.deleted_objects.filter(
                ministry=ministry,
                member=member
            ).first()

            if existing_deleted:
                existing_deleted.undelete()
                existing_deleted.is_active = True
                existing_deleted.save()
                messages.success(request, f'{member.name} reativado(a) no ministério {ministry.name}!')
            else:
                membership, created = MinistryMembership.objects.get_or_create(
                    ministry=ministry,
                    member=member,
                    defaults={
                        'role': 'member',
                        'is_active': True
                    }
                )

                if created:
                    if ministry in member.ministry.all():
                        member.ministry.remove(ministry)
                    messages.success(request, f'{member.name} adicionado(a) ao ministério {ministry.name}!')
                else:
                    if not membership.is_active:
                        membership.is_active = True
                        membership.save()
                        messages.success(request, f'{member.name} reativado(a) no ministério {ministry.name}!')
                    else:
                        if ministry in member.ministry.all():
                            member.ministry.remove(ministry)
                            messages.success(request, f'{member.name} já estava no ministério (sistema migrado)!')
                        else:
                            messages.warning(request, f'{member.name} já faz parte do ministério {ministry.name}!')
        except Exception:
            if ministry in member.ministry.all():
                member.ministry.remove(ministry)
            messages.warning(request, f'{member.name} já faz parte do ministério {ministry.name}!')

        return redirect('ministry_members', pk=ministry_id)

    def get(self, request, ministry_id, member_id):
        return self.post(request, ministry_id, member_id)


class MinistryRemoveMemberView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Remove um membro do ministério"""

    def post(self, request, ministry_id, member_id):
        ministry = get_object_or_404(Ministry, id=ministry_id)
        member = get_object_or_404(Member, id=member_id)

        removed = False

        membership = MinistryMembership.all_objects.filter(
            ministry=ministry,
            member=member
        ).first()

        if membership:
            membership.delete(force_policy=HARD_DELETE)
            removed = True

        if ministry in member.ministry.all():
            member.ministry.remove(ministry)
            removed = True

        if removed:
            messages.success(request, f'{member.name} removido(a) do ministério {ministry.name}!')
        else:
            messages.warning(request, f'{member.name} não faz parte do ministério {ministry.name}!')

        return redirect('ministry_members', pk=ministry_id)

    def get(self, request, ministry_id, member_id):
        return self.post(request, ministry_id, member_id)


class MinistryToggleRoleView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Alterna o papel do membro entre líder e membro"""

    def post(self, request, ministry_id, member_id):
        ministry = get_object_or_404(Ministry, id=ministry_id)
        member = get_object_or_404(Member, id=member_id)

        membership = MinistryMembership.objects.filter(
            ministry=ministry,
            member=member
        ).first()

        if not membership:
            if ministry in member.ministry.all():
                try:
                    membership, created = MinistryMembership.objects.get_or_create(
                        ministry=ministry,
                        member=member,
                        defaults={
                            'role': 'member',
                            'is_active': True
                        }
                    )
                    if created:
                        member.ministry.remove(ministry)
                except Exception:
                    membership = MinistryMembership.objects.filter(
                        ministry=ministry,
                        member=member
                    ).first()
                    if not membership:
                        messages.error(request, f'Erro ao processar {member.name}.')
                        return redirect('ministry_members', pk=ministry_id)
            else:
                messages.error(request, f'{member.name} não faz parte do ministério {ministry.name}!')
                return redirect('ministry_members', pk=ministry_id)

        if membership.role == 'leader':
            membership.role = 'member'
            messages.success(request, f'{member.name} não é mais líder do ministério {ministry.name}.')
        else:
            membership.role = 'leader'
            messages.success(request, f'{member.name} agora é líder do ministério {ministry.name}!')

        membership.save()

        return redirect('ministry_members', pk=ministry_id)

    def get(self, request, ministry_id, member_id):
        return self.post(request, ministry_id, member_id)


class MinistryToggleStatusView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Ativa/desativa um membro no ministério"""

    def post(self, request, ministry_id, member_id):
        ministry = get_object_or_404(Ministry, id=ministry_id)
        member = get_object_or_404(Member, id=member_id)

        membership = get_object_or_404(
            MinistryMembership,
            ministry=ministry,
            member=member
        )

        membership.is_active = not membership.is_active
        membership.save()

        status_text = 'ativo' if membership.is_active else 'inativo'
        messages.success(request, f'{member.name} está agora {status_text} no ministério {ministry.name}.')

        return redirect('ministry_members', pk=ministry_id)

    def get(self, request, ministry_id, member_id):
        return self.post(request, ministry_id, member_id)
