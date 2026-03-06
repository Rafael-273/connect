from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.http import JsonResponse
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone

from ..models.user import User
from ..models.member import Member
from ..models.ministry_membership import MinistryMembership
from .mixins import MemberRequiredMixin, ApproverRequiredMixin, MinistrationContextMixin


class MemberLoginView(View):
    """View de login unificada para membros e administradores"""
    
    def get(self, request):
        if request.user.is_authenticated:
            return redirect('member_dashboard')
        return render(request, 'member/login.html')
    
    def post(self, request):
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '').strip()
        
        if not email or not password:
            messages.error(request, 'Por favor, preencha todos os campos.')
            return render(request, 'member/login.html')
        
        user = authenticate(request, username=email, password=password)
        
        if user is not None:
            login(request, user)
            
            try:
                member = user.member
                messages.success(request, f'Bem-vindo(a), {member.name}!')
            except Member.DoesNotExist:
                messages.success(request, f'Bem-vindo(a)!')
            
            next_url = request.GET.get('next', 'member_dashboard')
            return redirect(next_url)
        else:
            messages.error(request, 'Email ou senha incorretos.')
        
        return render(request, 'member/login.html')


class MemberDashboardView(MemberRequiredMixin, MinistrationContextMixin, View):
    """Dashboard principal para membros logados"""
    
    def get(self, request):
        from datetime import datetime, timedelta
        from django.db.models import Q
        from website.models import WordOfKnowledge, Ministry
        from website.models.schedule import ScheduleDay, Team
        
        member = self.member
        
        today = datetime.now().date()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        
        is_scheduled = ScheduleDay.objects.filter(
            date__gte=week_start,
            date__lte=week_end,
            is_cancelled=False,
            schedule__ministry__name__icontains='ministração'
        ).filter(
            Q(members=member) |
            Q(team__members=member)
        ).exists()
        
        context = {
            'member': member,
            'ministries': member.ministry.all(),
            'can_consolidate': member.is_available_to_consolidate,
            'can_disciple': member.is_available_to_disciple,
            'is_scheduled_this_week': is_scheduled,
            'is_approver': member.is_approver,
            'is_ministration_member': self.get_ministration_status(member),
        }
        
        if member.is_approver:
            pending_words = WordOfKnowledge.objects.filter(
                is_approved=False
            ).select_related('member').order_by('service_date', 'recorded_at')
            context['pending_words'] = pending_words
            context['pending_words_count'] = pending_words.count()
        
        if hasattr(member, 'performed_followups'):
            context['followups_responsible'] = member.performed_followups.filter(
                end_date__isnull=True
            ).select_related('accompanied')[:5]
        
        if hasattr(member, 'received_followups'):
            context['followups_received'] = member.received_followups.filter(
                end_date__isnull=True
            ).select_related('responsible')[:5]
        
        return render(request, 'member/dashboard.html', context)


class MemberLogoutView(View):
    """Logout unificado para membros e administradores"""

    def get(self, request):
        if request.user.is_authenticated:
            user_name = request.user.member.name if hasattr(request.user, 'member') else request.user.email
            logout(request)
            messages.success(request, f'Até logo, {user_name}! Volte sempre.')
        return redirect('member_login')

    def post(self, request):
        return self.get(request)


class MemberProfileView(MemberRequiredMixin, MinistrationContextMixin, View):
    """Visualização e edição do perfil do membro"""

    def get(self, request):
        member = self.member
        from website.models import Neighborhood
        neighborhoods = Neighborhood.objects.all().order_by('name')

        context = {
            'member': member,
            'ministries': member.ministry.all(),
            'can_consolidate': member.is_available_to_consolidate,
            'is_ministration_member': self.get_ministration_status(member),
            'neighborhoods': neighborhoods,
        }
        return render(request, 'member/profile.html', context)

    def post(self, request):
        member = self.member
        form_type = request.POST.get('form_type')

        if form_type == 'personal_info':
            self._handle_personal_info(request, member)
        elif form_type == 'change_password':
            return self._handle_change_password(request, member)

        return redirect('member_profile')

    def _handle_personal_info(self, request, member):
        member.name = request.POST.get('name', member.name)
        member.phone = request.POST.get('phone', member.phone)
        member.address = request.POST.get('address', member.address)
        member.testimony = request.POST.get('testimony', member.testimony)

        birth_date = request.POST.get('birth_date')
        if birth_date:
            member.birth_date = birth_date

        neighborhood_id = request.POST.get('neighborhood')
        if neighborhood_id:
            from website.models import Neighborhood
            try:
                member.neighborhood = Neighborhood.objects.get(id=neighborhood_id)
            except Neighborhood.DoesNotExist:
                pass
        else:
            member.neighborhood = None

        if 'profile_picture' in request.FILES:
            profile_picture = request.FILES['profile_picture']
            if profile_picture.size > 5 * 1024 * 1024:
                messages.error(request, 'O arquivo é muito grande. Tamanho máximo: 5MB.')
                return
            allowed_types = ['image/jpeg', 'image/png', 'image/gif']
            if profile_picture.content_type not in allowed_types:
                messages.error(request, 'Formato de arquivo não suportado. Use JPG, PNG ou GIF.')
                return
            member.profile_picture = profile_picture

        try:
            member.save()
            messages.success(request, 'Informações pessoais atualizadas com sucesso!')
        except Exception as e:
            messages.error(request, f'Erro ao atualizar informações: {str(e)}')

    def _handle_change_password(self, request, member):
        current_password = request.POST.get('current_password')
        new_password = request.POST.get('new_password')
        confirm_password = request.POST.get('confirm_password')

        if not current_password or not new_password or not confirm_password:
            messages.error(request, 'Todos os campos de senha são obrigatórios.')
            return redirect('member_profile')

        if not request.user.check_password(current_password):
            messages.error(request, 'Senha atual incorreta.')
            return redirect('member_profile')

        if new_password != confirm_password:
            messages.error(request, 'As senhas não coincidem.')
            return redirect('member_profile')

        if len(new_password) < 6:
            messages.error(request, 'A nova senha deve ter pelo menos 6 caracteres.')
            return redirect('member_profile')

        try:
            request.user.set_password(new_password)
            request.user.save()
            messages.success(request, 'Senha alterada com sucesso! Faça login novamente.')
            logout(request)
            return redirect('member_login')
        except Exception as e:
            messages.error(request, f'Erro ao alterar senha: {str(e)}')

        return redirect('member_profile')


class MemberConsolidationView(MemberRequiredMixin, MinistrationContextMixin, View):
    """View para listar consolidados do membro logado"""

    def get(self, request):
        member = self.member
        consolidations = member.performed_followups.select_related(
            'accompanied'
        ).prefetch_related('reports').all()

        context = {
            'member': member,
            'consolidations': consolidations,
            'total_consolidations': consolidations.count(),
            'active_consolidations': consolidations.filter(end_date__isnull=True).count(),
            'completed_consolidations': consolidations.filter(end_date__isnull=False).count(),
            'can_consolidate': member.is_available_to_consolidate,
            'is_ministration_member': self.get_ministration_status(member),
        }
        return render(request, 'member/consolidation.html', context)


class MemberApproveWordView(ApproverRequiredMixin, View):
    """Permite que um membro aprovador aprove uma palavra de conhecimento"""

    def post(self, request, word_id):
        from website.models import WordOfKnowledge

        word = get_object_or_404(WordOfKnowledge, id=word_id)
        word.is_approved = True
        word.approved_by = self.member
        word.approved_at = timezone.now()
        word.save()

        messages.success(request, f'Palavra de {word.member.name} aprovada com sucesso!')

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'message': 'Palavra aprovada com sucesso!'
            })

        return redirect('member_dashboard')


class MemberRejectWordView(ApproverRequiredMixin, View):
    """Permite que um membro aprovador rejeite uma palavra de conhecimento"""

    def post(self, request, word_id):
        from website.models import WordOfKnowledge

        word = get_object_or_404(WordOfKnowledge, id=word_id)
        member_name = word.member.name
        word.delete()

        messages.warning(request, f'Palavra de {member_name} foi rejeitada e removida.')

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'message': 'Palavra rejeitada com sucesso!'
            })

        return redirect('member_dashboard')


class RedirectAfterLoginView(LoginRequiredMixin, View):
    """Redireciona o usuário para a página apropriada após login"""

    def get(self, request):
        user = request.user

        if user.has_admin_access():
            return redirect('admin_dashboard')

        if hasattr(user, 'member'):
            return redirect('member_dashboard')

        messages.error(request, 'Acesso negado. Você precisa estar cadastrado como membro ou ter permissões de administrador.')
        logout(request)
        return redirect('member_login')
