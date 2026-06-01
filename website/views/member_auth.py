from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.http import JsonResponse
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone

from django.db.models import Q

from ..models.user import User
from ..models.member import Member
from ..models.ministry_membership import MinistryMembership
from ..models.schedule import MonthlySchedule
from .mixins import MemberRequiredMixin, ApproverRequiredMixin, MinistrationContextMixin


class MemberLoginView(View):
    def get(self, request):
        if request.user.is_authenticated:
            return redirect('member_dashboard')
        return render(request, 'member/login.html')

    def post(self, request):
        if request.user.is_authenticated:
            return redirect('member_dashboard')

        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')

        if not email or not password:
            messages.error(request, 'Por favor, preencha todos os campos.')
            return render(request, 'member/login.html', {'email': email})

        user = authenticate(request, username=email, password=password)

        if user is None:
            messages.error(request, 'Email ou senha incorretos.')
            return render(request, 'member/login.html', {'email': email})

        login(request, user)

        member = getattr(user, 'member', None)
        name = member.name if member else user.email
        messages.success(request, f'Bem-vindo(a), {name}!')

        from django.utils.http import url_has_allowed_host_and_scheme
        next_url = request.POST.get('next') or request.GET.get('next', '')
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            return redirect(next_url)
        return redirect('member_dashboard')


class MemberDashboardView(MemberRequiredMixin, MinistrationContextMixin, View):
    def get(self, request):
        member = self.member
        context = self._build_base_context(member)
        self._add_followup_context(member, context)
        return render(request, 'member/dashboard.html', context)

    def _build_base_context(self, member):
        flags = self._get_ministry_flags(member)
        member_schedules = self._get_current_schedules(member)

        return {
            'member': member,
            'can_consolidate': flags['can_consolidate'],
            'is_approver': flags['is_approver'],
            'is_ministration_member': flags['is_ministration'],
            'is_media_member': flags['is_media'],
            'is_boas_vindas_member': flags['is_boas_vindas'],
            'is_moderacao_member': flags['is_moderacao'],
            'can_music_member': flags['has_ministries'],
            'member_schedules': member_schedules,
            'is_new_member': not any([flags['has_ministries'], flags['is_approver'], flags['can_consolidate']]),
            'is_house_of_peace_member': flags['has_ministries'],
        }

    def _get_ministry_flags(self, member):
        return {
            'has_ministries': member.ministry_memberships.filter(is_active=True).exists(),
            'is_approver': member.is_approver,
            'can_consolidate': member.is_available_to_consolidate,
            'is_ministration': self.get_ministration_status(member),
            'is_media': self.get_media_status(member),
            'is_boas_vindas': MinistryMembership.objects.filter(
                member=member,
                is_active=True,
                ministry__name__icontains='boas vindas',
            ).exists(),
            'is_moderacao': MinistryMembership.objects.filter(
                member=member,
                is_active=True,
                ministry__name__icontains='modera',
            ).exists(),
        }

    def _get_current_schedules(self, member):
        now = timezone.now()
        next_month = now.month % 12 + 1
        next_year = now.year + 1 if now.month == 12 else now.year
        ministry_ids = MinistryMembership.objects.filter(
            member=member, is_active=True
        ).values_list('ministry_id', flat=True)
        return list(
            MonthlySchedule.objects
            .filter(
                ministry_id__in=ministry_ids,
            )
            .filter(
                Q(month=now.month, year=now.year) |
                Q(month=next_month, year=next_year)
            )
            .select_related('ministry')
            .order_by('year', 'month', 'ministry__name', 'title')
        )

    def _add_followup_context(self, member, context):
        if hasattr(member, 'performed_followups'):
            context['followups_responsible'] = (
                member.performed_followups
                .filter(end_date__isnull=True)
                .select_related('accompanied')[:5]
            )
        if hasattr(member, 'received_followups'):
            context['followups_received'] = (
                member.received_followups
                .filter(end_date__isnull=True)
                .select_related('responsible')[:5]
            )


class MemberLogoutView(View):
    def get(self, request):
        if request.user.is_authenticated:
            user_name = request.user.member.name if hasattr(request.user, 'member') else request.user.email
            logout(request)
            messages.success(request, f'Até logo, {user_name}! Volte sempre.')
        return redirect('member_login')

    def post(self, request):
        return self.get(request)


class MemberProfileView(MemberRequiredMixin, MinistrationContextMixin, View):
    MAX_AVATAR_SIZE = 5 * 1024 * 1024
    ALLOWED_IMAGE_TYPES = frozenset(['image/jpeg', 'image/png', 'image/gif'])

    def get(self, request):
        return render(request, 'member/profile.html', self._build_context(self.member))

    def _build_context(self, member, profile_form=None, password_form=None):
        from website.forms.member import MemberProfileForm, MemberPasswordChangeForm
        return {
            'member': member,
            'profile_form': profile_form or MemberProfileForm(instance=member),
            'password_form': password_form or MemberPasswordChangeForm(user=self.request.user),
            'can_consolidate': member.is_available_to_consolidate,
            'is_ministration_member': self.get_ministration_status(member),
            'is_media_member': self.get_media_status(member),
        }

    def post(self, request):
        member = self.member
        form_type = request.POST.get('form_type')

        if form_type == 'personal_info':
            return self._handle_personal_info(request, member)
        elif form_type == 'change_password':
            return self._handle_change_password(request, member)

        return redirect('member_profile')

    def _handle_personal_info(self, request, member):
        from website.forms.member import MemberProfileForm
        form = MemberProfileForm(request.POST, instance=member)

        if not form.is_valid():
            messages.error(request, 'Por favor, corrija os erros abaixo.')
            return render(request, 'member/profile.html', self._build_context(member, profile_form=form))

        instance = form.save(commit=False)

        if not self._update_profile_picture(request, instance):
            return render(request, 'member/profile.html', self._build_context(member, profile_form=form))

        try:
            instance.save()
            messages.success(request, 'Informações pessoais atualizadas com sucesso!')
        except Exception as e:
            messages.error(request, f'Erro ao atualizar informações: {str(e)}')

        return redirect('member_profile')

    def _update_profile_picture(self, request, member):
        if 'profile_picture' not in request.FILES:
            return True

        picture = request.FILES['profile_picture']

        if picture.size > self.MAX_AVATAR_SIZE:
            messages.error(request, 'O arquivo é muito grande. Tamanho máximo: 5MB.')
            return False

        if picture.content_type not in self.ALLOWED_IMAGE_TYPES:
            messages.error(request, 'Formato de arquivo não suportado. Use JPG, PNG ou GIF.')
            return False

        member.profile_picture = picture
        return True

    def _handle_change_password(self, request, member):
        from website.forms.member import MemberPasswordChangeForm
        form = MemberPasswordChangeForm(user=request.user, data=request.POST)

        if not form.is_valid():
            return render(request, 'member/profile.html', self._build_context(member, password_form=form))

        try:
            form.save()
            messages.success(request, 'Senha alterada com sucesso! Faça login novamente.')
            logout(request)
            return redirect('member_login')
        except Exception as e:
            messages.error(request, f'Erro ao alterar senha: {str(e)}')

        return redirect('member_profile')


class MemberConsolidationView(MemberRequiredMixin, MinistrationContextMixin, View):
    def get(self, request):
        member = self.member
        consolidations = member.performed_followups.select_related(
            'accompanied'
        ).prefetch_related('reports').all()

        context = {
            'consolidations': consolidations,
            'total_consolidations': consolidations.count(),
            'active_consolidations': consolidations.filter(end_date__isnull=True).count(),
            'completed_consolidations': consolidations.filter(end_date__isnull=False).count(),
            'is_ministration_member': self.get_ministration_status(member),
            'is_media_member': self.get_media_status(member),
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


class MemberRoteiroView(MemberRequiredMixin, MinistrationContextMixin, View):
    """Exibe o roteiro de culto para membros."""

    def get(self, request):
        from ..models.roteiro import Roteiro
        roteiro, _ = Roteiro.objects.get_or_create(pk=1, defaults={'titulo': 'Roteiro de Culto'})
        anuncios = roteiro.anuncios.prefetch_related('datas', 'fotos').order_by('ordem', 'created_at')
        anuncios_ativos = [a for a in anuncios if not a.is_expired]
        return render(request, 'member/roteiro.html', {
            'roteiro': roteiro,
            'anuncios': anuncios_ativos,
        })


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
