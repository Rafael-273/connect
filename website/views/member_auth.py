from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.contrib.auth.mixins import LoginRequiredMixin

from ..models.user import User
from ..models.member import Member


class MemberLoginView(View):
    """View de login específica para membros da igreja"""
    
    def get(self, request):
        # Se o usuário já estiver logado e for membro, redireciona para o dashboard
        if request.user.is_authenticated and hasattr(request.user, 'member'):
            return redirect('member_dashboard')
        
        # Se for admin logado, redireciona para o painel admin
        if request.user.is_authenticated and request.user.has_admin_access():
            return redirect('admin_dashboard')
            
        return render(request, 'member/login.html')
    
    def post(self, request):
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '').strip()
        
        if not email or not password:
            messages.error(request, 'Por favor, preencha todos os campos.')
            return render(request, 'member/login.html')
        
        # Autentica o usuário
        user = authenticate(request, username=email, password=password)
        
        if user is not None:
            # Verifica se é um membro (não admin)
            try:
                member = user.member
                if user.user_type == 'member' and not user.has_admin_access():
                    login(request, user)
                    messages.success(request, f'Bem-vindo(a), {member.name}!')
                    
                    # Redireciona para a URL solicitada ou para o dashboard
                    next_url = request.GET.get('next', 'member_dashboard')
                    return redirect(next_url)
                else:
                    messages.error(request, 'Esta área é exclusiva para membros. Use o painel administrativo.')
            except Member.DoesNotExist:
                messages.error(request, 'Usuário não está cadastrado como membro da igreja.')
        else:
            messages.error(request, 'Email ou senha incorretos.')
        
        return render(request, 'member/login.html')


class MemberDashboardView(LoginRequiredMixin, View):
    """Dashboard principal para membros logados"""
    
    def dispatch(self, request, *args, **kwargs):
        # Verifica se o usuário é um membro (não admin)
        if not hasattr(request.user, 'member') or request.user.has_admin_access():
            messages.error(request, 'Acesso negado. Esta área é exclusiva para membros.')
            return redirect('member_login')
        
        return super().dispatch(request, *args, **kwargs)
    
    def get(self, request):
        member = request.user.member
        
        # Busca informações relevantes para o dashboard
        context = {
            'member': member,
            'ministries': member.ministry.all(),
            'can_consolidate': member.is_available_to_consolidate,
            'can_disciple': member.is_available_to_disciple,
        }
        
        # Busca acompanhamentos onde o membro é responsável
        if hasattr(member, 'performed_followups'):
            context['followups_responsible'] = member.performed_followups.filter(
                end_date__isnull=True
            ).select_related('accompanied')[:5]
        
        # Busca acompanhamentos onde o membro está sendo acompanhado
        if hasattr(member, 'received_followups'):
            context['followups_received'] = member.received_followups.filter(
                end_date__isnull=True
            ).select_related('responsible')[:5]
        
        return render(request, 'member/dashboard.html', context)


def member_logout_view(request):
    """Logout específico para membros"""
    if request.user.is_authenticated:
        member_name = request.user.member.name if hasattr(request.user, 'member') else request.user.email
        logout(request)
        messages.success(request, f'Até logo, {member_name}! Volte sempre.')
    
    return redirect('member_login')


@login_required
def member_profile_view(request):
    """Visualização e edição do perfil do membro"""
    if not hasattr(request.user, 'member') or request.user.has_admin_access():
        messages.error(request, 'Acesso negado.')
        return redirect('member_login')
    
    member = request.user.member
    
    if request.method == 'POST':
        # Campos que o membro pode editar
        member.phone = request.POST.get('phone', member.phone)
        member.address = request.POST.get('address', member.address)
        member.interests = request.POST.get('interests', member.interests)
        member.available_days = request.POST.get('available_days', member.available_days)
        member.testimony = request.POST.get('testimony', member.testimony)
        
        # Atualiza disponibilidade para ministério
        member.is_available_to_consolidate = 'is_available_to_consolidate' in request.POST
        member.is_available_to_disciple = 'is_available_to_disciple' in request.POST
        
        try:
            member.save()
            messages.success(request, 'Perfil atualizado com sucesso!')
        except Exception as e:
            messages.error(request, f'Erro ao atualizar perfil: {str(e)}')
        
        return redirect('member_profile')
    
    context = {
        'member': member,
        'ministries': member.ministry.all(),
    }
    
    return render(request, 'member/profile.html', context)
