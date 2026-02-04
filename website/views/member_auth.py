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
from ..models.ministry_membership import MinistryMembership


class MemberLoginView(View):
    """View de login unificada para membros e administradores"""
    
    def get(self, request):
        # Se o usuário já estiver logado, redireciona para dashboard de membros
        if request.user.is_authenticated:
            return redirect('member_dashboard')
            
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
            login(request, user)
            
            # Sempre redireciona para o dashboard de membros
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


class MemberDashboardView(LoginRequiredMixin, View):
    """Dashboard principal para membros logados"""
    
    def dispatch(self, request, *args, **kwargs):
        # Verifica se o usuário é um membro
        if not hasattr(request.user, 'member'):
            messages.error(request, 'Acesso negado. Você precisa estar cadastrado como membro.')
            return redirect('member_login')
        
        return super().dispatch(request, *args, **kwargs)
    
    def get(self, request):
        from datetime import datetime, timedelta
        from django.db.models import Q
        from website.models import WordOfKnowledge, Ministry
        from website.models.schedule import ScheduleDay, Team, MonthlySchedule
        from calendar import monthrange
        
        member = request.user.member
        
        # Verifica se o membro está escalado na semana atual (segunda a domingo)
        today = datetime.now().date()
        
        # Calcula o início e fim da semana atual
        week_start = today - timedelta(days=today.weekday())  # Segunda
        week_end = week_start + timedelta(days=6)  # Domingo
        
        # Verifica se o membro está escalado em algum dia desta semana
        # Pode estar diretamente em ScheduleDay.members OU em uma Team escalada
        # IMPORTANTE: Apenas para o ministério de ministração
        is_scheduled = ScheduleDay.objects.filter(
            date__gte=week_start,
            date__lte=week_end,
            is_cancelled=False,
            schedule__ministry__name__icontains='ministração'  # Apenas ministério de ministração
        ).filter(
            Q(members=member) |  # Escalado diretamente
            Q(team__members=member)  # Ou na equipe escalada
        ).exists()
        
        # Verifica se o membro está no ministério de ministração
        # Buscar no sistema antigo (member.ministry) E no novo sistema (MinistryMembership)
        from website.models import MinistryMembership
        
        is_ministration_old = member.ministry.filter(name__icontains='ministração').exists()
        is_ministration_new = MinistryMembership.objects.filter(
            member=member,
            ministry__name__icontains='ministração',
            is_active=True
        ).exists()
        
        is_ministration_member = is_ministration_old or is_ministration_new
        
        # Busca escalas do membro no mês atual e próximo
        current_month = today.month
        current_year = today.year
        next_month = current_month + 1 if current_month < 12 else 1
        next_year = current_year if current_month < 12 else current_year + 1
        
        # Busca ministérios do membro (sistema híbrido - antigo e novo)
        ministry_ids_old = member.ministry.values_list('id', flat=True)
        ministry_ids_new = MinistryMembership.objects.filter(
            member=member,
            is_active=True
        ).values_list('ministry_id', flat=True)
        
        # Unir os IDs dos dois sistemas
        all_ministry_ids = list(set(list(ministry_ids_old) + list(ministry_ids_new)))
        
        # DEBUG
        print(f"🔍 DEBUG - Membro: {member.name} (ID: {member.id})")
        print(f"🔍 DEBUG - Ministérios IDs: {all_ministry_ids}")
        print(f"🔍 DEBUG - Mês atual: {current_month}/{current_year}, Próximo: {next_month}/{next_year}")
        
        # Buscar escalas publicadas onde o membro está nos ministérios
        member_schedules = MonthlySchedule.objects.filter(
            Q(month=current_month, year=current_year) | Q(month=next_month, year=next_year),
            ministry_id__in=all_ministry_ids,
            deleted__isnull=True
        ).select_related('ministry').distinct()
        
        print(f"🔍 DEBUG - Escalas encontradas (antes de filtrar): {member_schedules.count()}")
        for s in member_schedules:
            print(f"   - {s.ministry.name}: {s.title} ({s.month}/{s.year})")
        
        # Filtrar apenas escalas onde o membro está realmente escalado
        schedules_with_member = []
        for schedule in member_schedules:
            # Verifica se o membro está em algum dia desta escala
            has_schedule = ScheduleDay.objects.filter(
                schedule=schedule,
                is_cancelled=False
            ).filter(
                Q(members=member) |  # Escalado diretamente
                Q(team__members=member)  # Ou na equipe escalada
            ).exists()
            
            print(f"🔍 DEBUG - Escala {schedule.title}: membro escalado = {has_schedule}")
            
            if has_schedule:
                schedules_with_member.append(schedule)
        
        has_schedules = len(schedules_with_member) > 0
        print(f"🔍 DEBUG - Total de escalas com o membro: {len(schedules_with_member)}")
        print(f"🔍 DEBUG - has_schedules: {has_schedules}")
        
        # Busca informações relevantes para o dashboard
        context = {
            'member': member,
            'ministries': member.ministry.all(),
            'can_consolidate': member.is_available_to_consolidate,
            'can_disciple': member.is_available_to_disciple,
            'is_scheduled_this_week': is_scheduled,
            'is_approver': member.is_approver,
            'is_ministration_member': is_ministration_member,
            'has_schedules': has_schedules,
            'member_schedules': schedules_with_member,
        }
        
        # Se for aprovador, busca palavras pendentes de aprovação
        if member.is_approver:
            pending_words = WordOfKnowledge.objects.filter(
                is_approved=False
            ).select_related('member').order_by('service_date', 'recorded_at')
            context['pending_words'] = pending_words
            context['pending_words_count'] = pending_words.count()
        
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
    """Logout unificado para membros e administradores"""
    if request.user.is_authenticated:
        user_name = request.user.member.name if hasattr(request.user, 'member') else request.user.email
        logout(request)
        messages.success(request, f'Até logo, {user_name}! Volte sempre.')
    
    return redirect('member_login')


@login_required
def member_profile_view(request):
    """Visualização e edição do perfil do membro"""
    if not hasattr(request.user, 'member'):
        messages.error(request, 'Acesso negado.')
        return redirect('member_login')
    
    member = request.user.member
    
    if request.method == 'POST':
        form_type = request.POST.get('form_type')
        
        if form_type == 'personal_info':
            # Atualização de informações pessoais
            member.name = request.POST.get('name', member.name)
            member.phone = request.POST.get('phone', member.phone)
            member.address = request.POST.get('address', member.address)
            member.testimony = request.POST.get('testimony', member.testimony)
            
            # Data de nascimento
            birth_date = request.POST.get('birth_date')
            if birth_date:
                member.birth_date = birth_date
            
            # Bairro
            neighborhood_id = request.POST.get('neighborhood')
            if neighborhood_id:
                from website.models import Neighborhood
                try:
                    member.neighborhood = Neighborhood.objects.get(id=neighborhood_id)
                except Neighborhood.DoesNotExist:
                    pass
            else:
                member.neighborhood = None
            
            # Upload de foto de perfil
            neighborhood_id = request.POST.get('neighborhood')
            if neighborhood_id:
                from website.models import Neighborhood
                try:
                    member.neighborhood = Neighborhood.objects.get(id=neighborhood_id)
                except Neighborhood.DoesNotExist:
                    pass
            else:
                member.neighborhood = None
            
            # Upload de foto de perfil
            if 'profile_picture' in request.FILES:
                profile_picture = request.FILES['profile_picture']
                # Validação do arquivo
                if profile_picture.size > 5 * 1024 * 1024:  # 5MB
                    messages.error(request, 'O arquivo é muito grande. Tamanho máximo: 5MB.')
                    return redirect('member_profile')
                
                allowed_types = ['image/jpeg', 'image/png', 'image/gif']
                if profile_picture.content_type not in allowed_types:
                    messages.error(request, 'Formato de arquivo não suportado. Use JPG, PNG ou GIF.')
                    return redirect('member_profile')
                
                member.profile_picture = profile_picture
            
            try:
                member.save()
                messages.success(request, 'Informações pessoais atualizadas com sucesso!')
            except Exception as e:
                messages.error(request, f'Erro ao atualizar informações: {str(e)}')
                
        elif form_type == 'change_password':
            # Alteração de senha
            current_password = request.POST.get('current_password')
            new_password = request.POST.get('new_password')
            confirm_password = request.POST.get('confirm_password')
            
            # Validações
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
    
    # Verificar se pode consolidar (para o menu)
    can_consolidate = member.is_available_to_consolidate
    
    # Obter todos os bairros
    from website.models import Neighborhood
    neighborhoods = Neighborhood.objects.all().order_by('name')
    
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
        'ministries': member.ministry.all(),
        'can_consolidate': can_consolidate,
        'is_ministration_member': is_ministration_member,
        'neighborhoods': neighborhoods,
    }
    
    return render(request, 'member/profile.html', context)


@login_required
def member_consolidation_view(request):
    """View para listar consolidados do membro logado"""
    member = request.user.member
    
    consolidations = member.performed_followups.select_related('accompanied').prefetch_related('reports').all()
    
    total_consolidations = consolidations.count()
    active_consolidations = consolidations.filter(end_date__isnull=True).count()
    completed_consolidations = consolidations.filter(end_date__isnull=False).count()
    
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
        'consolidations': consolidations,
        'total_consolidations': total_consolidations,
        'active_consolidations': active_consolidations,
        'completed_consolidations': completed_consolidations,
        'can_consolidate': member.is_available_to_consolidate,
        'is_ministration_member': is_ministration_member,
    }
    
    return render(request, 'member/consolidation.html', context)


@login_required
def member_approve_word(request, word_id):
    """Permite que um membro aprovador aprove uma palavra de conhecimento"""
    if request.method != 'POST':
        messages.error(request, 'Método não permitido.')
        return redirect('member_dashboard')
    
    # Verificar se o membro é aprovador
    try:
        member = request.user.member
        if not member.is_approver:
            messages.error(request, 'Você não tem permissão para aprovar palavras.')
            return redirect('member_dashboard')
    except:
        messages.error(request, 'Acesso negado.')
        return redirect('member_login')
    
    from website.models import WordOfKnowledge
    from django.utils import timezone
    from django.shortcuts import get_object_or_404
    
    word = get_object_or_404(WordOfKnowledge, id=word_id)
    
    # Aprovar a palavra
    word.is_approved = True
    word.approved_by = member
    word.approved_at = timezone.now()
    word.save()
    
    messages.success(request, f'Palavra de {word.member.name} aprovada com sucesso!')
    
    # Se for requisição AJAX, retorna JSON
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'success': True,
            'message': 'Palavra aprovada com sucesso!'
        })
    
    return redirect('member_dashboard')


@login_required
def member_reject_word(request, word_id):
    """Permite que um membro aprovador rejeite uma palavra de conhecimento"""
    if request.method != 'POST':
        messages.error(request, 'Método não permitido.')
        return redirect('member_dashboard')
    
    # Verificar se o membro é aprovador
    try:
        member = request.user.member
        if not member.is_approver:
            messages.error(request, 'Você não tem permissão para rejeitar palavras.')
            return redirect('member_dashboard')
    except:
        messages.error(request, 'Acesso negado.')
        return redirect('member_login')
    
    from website.models import WordOfKnowledge
    from django.shortcuts import get_object_or_404
    
    word = get_object_or_404(WordOfKnowledge, id=word_id)
    member_name = word.member.name
    word.delete()
    
    messages.warning(request, f'Palavra de {member_name} foi rejeitada e removida.')
    
    # Se for requisição AJAX, retorna JSON
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'success': True,
            'message': 'Palavra rejeitada com sucesso!'
        })
    
    return redirect('member_dashboard')


@login_required
def redirect_after_login(request):
    """Redireciona o usuário para a página apropriada após login"""
    user = request.user
    
    # Se é admin ou tem acesso ao painel, vai para admin-panel
    if user.has_admin_access():
        return redirect('admin_dashboard')
    
    # Se é membro, vai para dashboard de membros
    if hasattr(user, 'member'):
        return redirect('member_dashboard')
    
    # Se não tem nenhum dos dois, mostra mensagem de erro
    messages.error(request, 'Acesso negado. Você precisa estar cadastrado como membro ou ter permissões de administrador.')
    logout(request)
    return redirect('member_login')


@login_required
def get_schedule_days(request, schedule_id):
    """Retorna os dias de uma escala específica via AJAX"""
    print(f"🔍 get_schedule_days chamada! schedule_id={schedule_id}, user={request.user}")
    
    try:
        from django.db.models import Q
        from website.models.schedule import MonthlySchedule, ScheduleDay
        from django.shortcuts import get_object_or_404
        
        # Verificar se o usuário é membro
        if not hasattr(request.user, 'member'):
            print("❌ Usuário não tem atributo member")
            return JsonResponse({'error': 'Acesso negado'}, status=403)
        
        member = request.user.member
        print(f"✅ Member encontrado: {member.name}")
        
        # Buscar a escala (removido is_published=True para permitir escalas não publicadas)
        schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)
        print(f"✅ Schedule encontrado: {schedule.title}")
        
    except Exception as e:
        print(f"❌ ERRO em get_schedule_days: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)
    
    # Verificar se o membro faz parte do ministério desta escala
    from website.models import MinistryMembership
    ministry_ids_old = member.ministry.values_list('id', flat=True)
    ministry_ids_new = MinistryMembership.objects.filter(
        member=member,
        is_active=True
    ).values_list('ministry_id', flat=True)
    
    all_ministry_ids = list(set(list(ministry_ids_old) + list(ministry_ids_new)))
    
    if schedule.ministry_id not in all_ministry_ids:
        return JsonResponse({'error': 'Você não faz parte deste ministério'}, status=403)
    
    # Buscar todos os dias da escala ordenados por data
    days = ScheduleDay.objects.filter(
        schedule=schedule
    ).select_related('team').prefetch_related('members').order_by('date')
    
    # Preparar dados para retornar
    days_data = []
    for day in days:
        # Verificar se o membro está escalado neste dia
        is_user_scheduled = False
        
        # Verifica se está diretamente nos membros
        if member in day.members.all():
            is_user_scheduled = True
        
        # Verifica se está na equipe escalada
        if day.team and member in day.team.members.all():
            is_user_scheduled = True
        
        # Informações da equipe
        team_name = day.team.name if day.team else None
        
        # Informações dos membros
        member_names = [m.name for m in day.members.all()] if day.members.exists() else []
        
        days_data.append({
            'date': day.date.isoformat(),
            'description': day.description or '',
            'notes': day.notes or '',
            'is_cancelled': day.is_cancelled,
            'cancellation_reason': day.cancellation_reason or '',
            'team_name': team_name,
            'member_names': member_names,
            'is_user_scheduled': is_user_scheduled,
        })
    
    return JsonResponse({
        'days': days_data,
        'schedule': {
            'id': schedule.id,
            'title': schedule.title,
            'ministry': schedule.ministry.name,
            'month': schedule.month,
            'year': schedule.year,
        }
    })


@login_required
def member_schedule_detail_view(request, schedule_id):
    """Visualização de escala para membros"""
    from website.models.schedule import MonthlySchedule, ScheduleDay
    from website.models import MinistryMembership
    from django.shortcuts import get_object_or_404
    
    # Verificar se o usuário é membro
    if not hasattr(request.user, 'member'):
        return redirect('member_dashboard')
    
    member = request.user.member
    schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)
    
    # Verificar se o membro faz parte do ministério desta escala
    ministry_ids_old = member.ministry.values_list('id', flat=True)
    ministry_ids_new = MinistryMembership.objects.filter(
        member=member,
        is_active=True
    ).values_list('ministry_id', flat=True)
    
    all_ministry_ids = list(set(list(ministry_ids_old) + list(ministry_ids_new)))
    
    if schedule.ministry_id not in all_ministry_ids:
        messages.error(request, 'Você não tem permissão para visualizar esta escala.')
        return redirect('member_dashboard')
    
    # Buscar todos os dias da escala
    days = ScheduleDay.objects.filter(
        schedule=schedule,
        deleted__isnull=True
    ).select_related('team').prefetch_related('members').order_by('date')
    
    # Organizar dias por semana
    WEEKDAYS_PT = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
    weeks = {}
    for day in days:
        # Adicionar nome do dia em português
        try:
            day.pt_weekday = WEEKDAYS_PT[day.date.weekday()]
        except Exception:
            day.pt_weekday = ''
        
        # Verificar se o membro está escalado neste dia
        day.is_user_scheduled = False
        if member in day.members.all():
            day.is_user_scheduled = True
        if day.team and member in day.team.members.all():
            day.is_user_scheduled = True
        
        week_num = day.get_week_number()
        if week_num not in weeks:
            weeks[week_num] = []
        weeks[week_num].append(day)
    
    context = {
        'schedule': schedule,
        'days': days,
        'weeks': weeks,
    }
    
    return render(request, 'member/schedule_detail.html', context)
