import uuid
from django.contrib.auth import get_user_model
from django.contrib.auth import update_session_auth_hash

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Count, Q, Sum, Avg
from django.utils import timezone
from django.utils.text import slugify
from datetime import datetime, timedelta
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json

from ..models.user import User
from ..models.member import Member
from ..models.visitor import Visitor
from ..models.event import Event
from ..models.ministry import Ministry
from ..models.neighborhood import Neighborhood
from ..models.evangelism import Evangelized
from ..models.follow_up import FollowUp, FollowUpReport, FollowUpTemplate, FollowUpTemplateStep
from ..models.canteen import CanteenDebtor
from ..forms.ministry import MinistryForm
from ..forms.neighborhood import NeighborhoodForm
from ..forms.follow_up import FollowUpForm, FollowUpReportForm
from ..forms.template import FollowUpTemplateForm, FollowUpTemplateStepFormSet, FollowUpTemplateStepFormSetForCreate
from ..forms.user import UserProfileForm
from ..forms.canteen import CanteenDebtorForm

User = get_user_model()

def is_admin(user):
    """Verifica se o usuário tem acesso ao painel administrativo"""
    return user.is_authenticated and user.has_admin_access()

def has_module_permission(module_name):
    """Retorna uma função que verifica se o usuário tem permissão para um módulo específico"""
    def check_permission(user):
        return user.is_authenticated and (user.is_superuser or user.has_module_permission(module_name))
    return check_permission


@user_passes_test(is_admin)
def dashboard_view(request):
    """Dashboard principal com estatísticas e métricas importantes"""
    
    # Estatísticas gerais
    total_members = Member.objects.filter(is_active=True).count()
    total_visitors = Visitor.objects.count()
    total_events = Event.objects.count()
    total_ministries = Ministry.objects.count()
    
    # Visitantes nos últimos 30 dias
    last_month = timezone.now() - timedelta(days=30)
    recent_visitors = Visitor.objects.filter(visit_date__gte=last_month).count()
    
    # Eventos próximos (próximos 30 dias)
    next_month = timezone.now() + timedelta(days=30)
    upcoming_events = Event.objects.filter(
        event_date__gte=timezone.now().date(),
        event_date__lte=next_month.date()
    ).count()
    
    # Conversões nos últimos 30 dias (visitantes com decisão por Jesus + membros novos convertidos)
    recent_visitor_conversions = Visitor.objects.filter(
        visit_date__gte=last_month,
        decision_for_jesus=True
    ).count()
    
    recent_member_conversions = Member.objects.filter(
        conversion_date__gte=last_month.date(),
        conversion='new_convert'
    ).count()
    
    recent_conversions = recent_visitor_conversions + recent_member_conversions
    
    # Gráfico de visitantes por mês (últimos 6 meses)
    six_months_ago = timezone.now() - timedelta(days=180)
    visitors_by_month = []
    for i in range(6):
        month_start = (timezone.now() - timedelta(days=30*i)).replace(day=1)
        month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        count = Visitor.objects.filter(
            visit_date__gte=month_start,
            visit_date__lte=month_end
        ).count()
        visitors_by_month.append({
            'month': month_start.strftime('%b/%Y'),
            'count': count
        })
    visitors_by_month.reverse()
    
    # Converter para JSON para uso no template
    import json
    visitors_by_month_json = json.dumps(visitors_by_month)
    
    # Ministérios com mais membros
    ministries_stats = Ministry.objects.annotate(
        member_count=Count('member')
    ).order_by('-member_count')[:5]
    
    # Últimos visitantes
    recent_visitors_list = Visitor.objects.order_by('-visit_date')[:5]
    
    recently_converted_members = Member.objects.filter(
        conversion='new_convert'
    ).order_by('-update_at')[:3]
    
    # Próximos eventos
    upcoming_events_list = Event.objects.filter(
        event_date__gte=timezone.now().date()
    ).order_by('event_date')[:5]
    
    context = {
        'total_members': total_members,
        'total_visitors': total_visitors,
        'total_events': total_events,
        'total_ministries': total_ministries,
        'recent_visitors': recent_visitors,
        'upcoming_events': upcoming_events,
        'recent_conversions': recent_conversions,
        'recent_visitor_conversions': recent_visitor_conversions,
        'recent_member_conversions': recent_member_conversions,
        'visitors_by_month_json': visitors_by_month_json,
        'ministries_stats': ministries_stats,
        'recent_visitors_list': recent_visitors_list,
        'recently_converted_members': recently_converted_members,
        'upcoming_events_list': upcoming_events_list,
    }
    
    return render(request, 'admin_panel/dashboard.html', context)


@user_passes_test(is_admin)
def members_list_view(request):
    """Lista de membros com filtros e busca"""
    members = Member.objects.select_related('user', 'neighborhood', 'spouse').all().order_by('-id')
    
    # Filtros
    search = request.GET.get('search', '')
    ministry_filter = request.GET.get('ministry', '')
    status_filter = request.GET.get('status', '')
    
    if search:
        members = members.filter(
            Q(name__icontains=search) |
            Q(user__email__icontains=search) |
            Q(phone__icontains=search)
        )
    
    if ministry_filter:
        members = members.filter(ministry_id=ministry_filter)
    
    if status_filter:
        if status_filter == 'active':
            members = members.filter(is_active=True)
        elif status_filter == 'inactive':
            members = members.filter(is_active=False)
        elif status_filter == 'new_convert':
            members = members.filter(conversion='new_convert')
    
    # Paginação
    paginator = Paginator(members, 20)
    page = request.GET.get('page')
    members = paginator.get_page(page)
    
    ministries = Ministry.objects.all()
    
    if 'member_success_message' in request.session:
        messages.success(request, request.session['member_success_message'])
        del request.session['member_success_message']
    
    context = {
        'members': members,
        'ministries': ministries,
        'search': search,
        'ministry_filter': ministry_filter,
        'status_filter': status_filter,
    }
    
    return render(request, 'admin_panel/members/list.html', context)


@user_passes_test(has_module_permission('visitors'))
def visitors_list_view(request):
    """Lista de visitantes com filtros e busca"""
    visitors = Visitor.objects.select_related('neighborhood').all().order_by('-visit_date', '-id')
    
    # Filtros
    search = request.GET.get('search', '')
    period_filter = request.GET.get('period', '')
    conversion_filter = request.GET.get('conversion', '')
    
    if search:
        visitors = visitors.filter(
            Q(name__icontains=search) |
            Q(email__icontains=search) |
            Q(phone__icontains=search)
        )
    
    if period_filter:
        if period_filter == '7_days':
            date_filter = timezone.now() - timedelta(days=7)
            visitors = visitors.filter(visit_date__gte=date_filter)
        elif period_filter == '30_days':
            date_filter = timezone.now() - timedelta(days=30)
            visitors = visitors.filter(visit_date__gte=date_filter)
    
    if conversion_filter:
        if conversion_filter == 'converted':
            visitors = visitors.filter(decision_for_jesus=True)
        elif conversion_filter == 'not_converted':
            visitors = visitors.filter(decision_for_jesus=False)
    
    # Paginação
    paginator = Paginator(visitors, 20)
    page = request.GET.get('page')
    visitors = paginator.get_page(page)
    
    context = {
        'visitors': visitors,
        'search': search,
        'period_filter': period_filter,
        'conversion_filter': conversion_filter,
    }
    
    return render(request, 'admin_panel/visitors/list.html', context)


@user_passes_test(has_module_permission('events'))
def events_list_view(request):
    events = Event.objects.all()
    
    search = request.GET.get('search', '')
    status_filter = request.GET.get('status', '')
    
    if search:
        events = events.filter(
            Q(title__icontains=search) |
            Q(description__icontains=search) |
            Q(location__icontains=search)
        )
    
    if status_filter:
        today = timezone.now().date()
        if status_filter == 'upcoming':
            events = events.filter(event_date__gte=today)
        elif status_filter == 'past':
            events = events.filter(event_date__lt=today)
    
    events = events.order_by('-event_date')
    
    # Paginação
    paginator = Paginator(events, 20)
    page = request.GET.get('page')
    events = paginator.get_page(page)
    
    context = {
        'events': events,
        'search': search,
        'status_filter': status_filter,
    }
    
    return render(request, 'admin_panel/events/list.html', context)


@user_passes_test(is_admin)
def ministries_list_view(request):
    """Lista de ministérios"""
    ministries = Ministry.objects.annotate(
        member_count=Count('member')
    ).order_by('name')
    
    search = request.GET.get('search', '')
    if search:
        ministries = ministries.filter(name__icontains=search)
    
    # Paginação
    paginator = Paginator(ministries, 20)
    page = request.GET.get('page')
    ministries = paginator.get_page(page)
    
    context = {
        'ministries': ministries,
        'search': search,
    }
    
    return render(request, 'admin_panel/ministries/list.html', context)


@user_passes_test(is_admin)
def neighborhoods_list_view(request):
    """Lista de bairros"""
    neighborhoods = Neighborhood.objects.select_related('parent').order_by('name')
    
    search = request.GET.get('search', '')
    if search:
        neighborhoods = neighborhoods.filter(name__icontains=search)
    
    # Paginação
    paginator = Paginator(neighborhoods, 20)
    page = request.GET.get('page')
    neighborhoods = paginator.get_page(page)
    
    context = {
        'neighborhoods': neighborhoods,
        'search': search,
    }
    
    return render(request, 'admin_panel/neighborhoods/list.html', context)


@csrf_exempt
@user_passes_test(is_admin)
def api_delete_item(request):
    """API para deletar itens via AJAX"""
    if request.method == 'POST':
        data = json.loads(request.body)
        model_name = data.get('model')
        item_id = data.get('id')
        
        try:
            if model_name == 'member':
                item = get_object_or_404(Member, id=item_id)
            elif model_name == 'visitor':
                item = get_object_or_404(Visitor, id=item_id)
            elif model_name == 'event':
                item = get_object_or_404(Event, id=item_id)
            elif model_name == 'ministry':
                item = get_object_or_404(Ministry, id=item_id)
            elif model_name == 'neighborhood':
                item = get_object_or_404(Neighborhood, id=item_id)
            else:
                return JsonResponse({'success': False, 'error': 'Modelo inválido'})
            
            item.delete()
            return JsonResponse({'success': True})
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Método não permitido'})


@csrf_exempt
@user_passes_test(is_admin)
def ministry_create_edit_api(request):
    """API para criar/editar ministérios via AJAX"""
    if request.method == 'POST':
        data = json.loads(request.body)
        ministry_id = data.get('id')
        name = data.get('name')
        description = data.get('description', '')
        
        try:
            if ministry_id:
                # Editar ministério existente
                ministry = get_object_or_404(Ministry, id=ministry_id)
                ministry.name = name
                ministry.description = description
                ministry.save()
                return JsonResponse({'success': True, 'message': 'Ministério atualizado com sucesso!'})
            else:
                # Criar novo ministério
                ministry = Ministry.objects.create(name=name, description=description)
                return JsonResponse({'success': True, 'message': 'Ministério criado com sucesso!'})
                
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Método não permitido'})


@csrf_exempt
@user_passes_test(is_admin)
def neighborhood_create_edit_api(request):
    """API para criar/editar bairros via AJAX"""
    if request.method == 'POST':
        data = json.loads(request.body)
        neighborhood_id = data.get('id')
        name = data.get('name')
        parent_id = data.get('parent')
        
        try:
            parent = None
            if parent_id:
                parent = get_object_or_404(Neighborhood, id=parent_id)
            
            if neighborhood_id:
                # Editar bairro existente
                neighborhood = get_object_or_404(Neighborhood, id=neighborhood_id)
                neighborhood.name = name
                neighborhood.parent = parent
                neighborhood.save()
                return JsonResponse({'success': True, 'message': 'Bairro atualizado com sucesso!'})
            else:
                # Criar novo bairro
                neighborhood = Neighborhood.objects.create(name=name, parent=parent)
                return JsonResponse({'success': True, 'message': 'Bairro criado com sucesso!'})
                
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Método não permitido'})


@user_passes_test(is_admin)
def member_detail_api(request, member_id):
    """API para obter detalhes de um membro"""
    member = get_object_or_404(Member, id=member_id)
    
    data = {
        'id': member.id,
        'name': member.name,
        'email': member.email,
        'phone': member.phone,
        'address': member.address,
        'birth_date': member.birth_date.strftime('%d/%m/%Y') if member.birth_date else None,
        'gender': member.get_gender_display() if member.gender else None,
        'ministry': member.ministry.name if member.ministry else None,
        'neighborhood': member.neighborhood.name if member.neighborhood else None,
        'marital_status': member.get_marital_status_display() if member.marital_status else None,
        'conversion': member.get_conversion_display() if member.conversion else None,
        'conversion_date': member.conversion_date.strftime('%d/%m/%Y') if member.conversion_date else None,
        'is_active': member.is_active,
        'testimony': member.testimony,
        'interests': member.interests,
        'profile_picture': member.profile_picture.url if member.profile_picture else None,
    }
    
    return JsonResponse(data)


@user_passes_test(is_admin)
def visitor_detail_api(request, visitor_id):
    """API para obter detalhes de um visitante"""
    visitor = get_object_or_404(Visitor, id=visitor_id)
    
    data = {
        'id': visitor.id,
        'name': visitor.name,
        'email': visitor.email,
        'phone': visitor.phone,
        'address': visitor.address,
        'gender': visitor.get_gender_display() if visitor.gender else None,
        'neighborhood': visitor.neighborhood.name if visitor.neighborhood else None,
        'visit_date': visitor.visit_date.strftime('%d/%m/%Y'),
        'decision_for_jesus': visitor.decision_for_jesus,
        'conversion': visitor.get_conversion_display() if visitor.conversion else None,
        'prayer_request': visitor.prayer_request,
        'profile_notes': visitor.profile_notes,
    }
    
    return JsonResponse(data)


@user_passes_test(is_admin)
def member_edit_view(request, member_id=None):
    """Criar ou editar membro"""
    member = None
    if member_id:
        member = get_object_or_404(Member, id=member_id)
    
    # Limpar mensagens antigas em GET requests para evitar mensagens persistentes
    if request.method == 'GET':
        # Limpar mensagens da sessão para evitar mensagens antigas
        storage = messages.get_messages(request)
        storage.used = True
    
    if request.method == 'POST':
        try:
            # Get form data
            name = request.POST.get('name')
            email = request.POST.get('email') or None
            phone = request.POST.get('phone') or None
            address = request.POST.get('address') or None
            birth_date = request.POST.get('birth_date') or None
            gender = request.POST.get('gender') or None
            marital_status = request.POST.get('marital_status') or None
            ministry_ids = request.POST.getlist('ministry')
            neighborhood_id = request.POST.get('neighborhood') or None
            conversion = request.POST.get('conversion') or None
            conversion_date = request.POST.get('conversion_date') or None
            is_active = request.POST.get('is_active') == 'on'  # Checkbox handling
            testimony = request.POST.get('testimony') or None
            interests = request.POST.get('interests') or None
            tags = request.POST.get('tags') or None
            spouse_id = request.POST.get('spouse') or None
            personality_type = request.POST.get('personality_type') or None
            initial_challenges = request.POST.get('initial_challenges') or None
            available_days = request.POST.get('available_days') or None
            is_available_to_consolidate = request.POST.get('is_available_to_consolidate') == 'on'
            is_available_to_disciple = request.POST.get('is_available_to_disciple') == 'on'
            is_approver = request.POST.get('is_approver') == 'on'
            user_type = request.POST.get('user_type') or 'member'  # Tipo de usuário, default: membro normal
            
            # Validações obrigatórias
            if not name:
                messages.error(request, 'Nome é obrigatório.')
                raise ValueError('Nome é obrigatório')
            
            # Get related objects
            ministries = []
            if ministry_ids:
                ministries = list(Ministry.objects.filter(id__in=ministry_ids))
            
            neighborhood = None
            if neighborhood_id:
                neighborhood = Neighborhood.objects.get(id=neighborhood_id)
            
            spouse = None
            if spouse_id:
                spouse = Member.objects.get(id=spouse_id)
            
            if member:
                # Update existing member
                member.name = name
                member.phone = phone
                member.address = address
                member.birth_date = birth_date
                member.gender = gender
                member.marital_status = marital_status
                member.neighborhood = neighborhood
                member.conversion = conversion
                member.conversion_date = conversion_date
                member.is_active = is_active
                member.testimony = testimony
                member.interests = interests
                member.tags = tags
                member.spouse = spouse
                member.personality_type = personality_type
                member.initial_challenges = initial_challenges
                member.available_days = available_days
                member.is_available_to_consolidate = is_available_to_consolidate
                member.is_available_to_disciple = is_available_to_disciple
                member.is_approver = is_approver
                
                # Update user email if provided
                if email and member.user:
                    member.user.email = email
                    # Atualizar tipo de usuário
                    member.user.user_type = user_type
                    # Atualizar is_staff com base no tipo de usuário
                    member.user.is_staff = user_type != 'member'
                    member.user.save()
                
                # Handle profile picture
                if 'profile_picture' in request.FILES:
                    member.profile_picture = request.FILES['profile_picture']
                
                member.save()
                member.ministry.set(ministries)
                request.session['member_success_message'] = 'Membro atualizado com sucesso!'
            else:
                # Create new member
                # Create user first
                if not email:
                    email = f"membro_{uuid.uuid4().hex[:8]}@autogerado.com"
                
                user = User.objects.create_user(
                    email=email,
                    password='123',  # Senha padrão
                    user_type=user_type,  # Tipo de usuário
                    is_staff=user_type != 'member'  # Is staff baseado no tipo
                )
                
                member = Member.objects.create(
                    user=user,
                    name=name,
                    phone=phone,
                    address=address,
                    birth_date=birth_date,
                    gender=gender,
                    marital_status=marital_status,
                    neighborhood=neighborhood,
                    conversion=conversion,
                    conversion_date=conversion_date,
                    is_active=is_active if is_active is not None else True,  # Default to True
                    testimony=testimony,
                    interests=interests,
                    tags=tags,
                    spouse=spouse,
                    personality_type=personality_type,
                    initial_challenges=initial_challenges,
                    available_days=available_days,
                    is_available_to_consolidate=is_available_to_consolidate,
                    is_available_to_disciple=is_available_to_disciple,
                    is_approver=is_approver
                )
                member.ministry.set(ministries)
                
                # Handle profile picture
                if 'profile_picture' in request.FILES:
                    member.profile_picture = request.FILES['profile_picture']
                    member.save()
                
                request.session['member_success_message'] = 'Membro criado com sucesso!'
            
            return redirect('admin_members_list')
            
        except Exception as e:
            error_msg = str(e)
            if 'duplicate key value' in error_msg and 'email' in error_msg:
                messages.error(request, 'Já existe um usuário com este email. Por favor, escolha outro email.')
            elif 'duplicate key value' in error_msg:
                messages.error(request, f'Erro de integridade: {error_msg}')
            else:
                messages.error(request, f'Erro ao salvar membro: {error_msg}')
    
    # Get data for form
    ministries = Ministry.objects.all().order_by('name')
    neighborhoods = Neighborhood.objects.all().order_by('name')
    all_members = Member.objects.all().order_by('name')
    context = {
        'member': member,
        'ministries': ministries,
        'neighborhoods': neighborhoods,
        'all_members': all_members,
    }
    
    return render(request, 'admin_panel/members/edit.html', context)


@user_passes_test(has_module_permission('visitors'))
def visitor_edit_view(request, visitor_id=None):
    """Criar ou editar visitante"""
    visitor = None
    email_warning = None  # Inicializar variável para avisos de email
    
    if visitor_id:
        visitor = get_object_or_404(Visitor, id=visitor_id)
    
    # Limpar mensagens antigas em GET requests para evitar mensagens persistentes
    if request.method == 'GET':
        # Limpar mensagens da sessão para evitar mensagens antigas
        storage = messages.get_messages(request)
        storage.used = True
    
    if request.method == 'POST':
        print("=== DEBUG: Dados recebidos no POST ===")
        for key, value in request.POST.items():
            print(f"{key}: {value}")
        print("=====================================")
        
        try:
            # Get form data
            name = request.POST.get('name', '').strip()
            email = request.POST.get('email', '').strip() or None
            phone = request.POST.get('phone', '').strip() or None
            address = request.POST.get('address', '').strip() or None
            visit_date = request.POST.get('visit_date', '').strip()
            gender = request.POST.get('gender', '').strip() or None
            neighborhood_id = request.POST.get('neighborhood', '').strip() or None
            decision_for_jesus = request.POST.get('decision_for_jesus') == 'true' or request.POST.get('decision_for_jesus') == 'on'
            conversion = request.POST.get('conversion', '').strip() or None
            prayer_request = request.POST.get('prayer_request', '').strip() or None
            profile_notes = request.POST.get('profile_notes', '').strip() or None
            
            # Validações obrigatórias
            if not name:
                messages.error(request, 'Nome é obrigatório.')
                raise ValueError('Nome é obrigatório')
            
            if not visit_date:
                messages.error(request, 'Data da visita é obrigatória.')
                raise ValueError('Data da visita é obrigatória')
            
            # Validar formato da data
            try:
                from datetime import datetime
                datetime.strptime(visit_date, '%Y-%m-%d')
            except ValueError:
                messages.error(request, 'Formato de data inválido. Use AAAA-MM-DD.')
                raise ValueError('Formato de data inválido')
            
            # Validar email único para visitantes (se fornecido)
            if email:
                existing_visitor = Visitor.objects.filter(email=email)
                if visitor:
                    # Excluir o visitante atual da verificação
                    existing_visitor = existing_visitor.exclude(id=visitor.id)
                
                if existing_visitor.exists():
                    messages.error(request, f'Já existe um visitante com o email "{email}". Por favor, use um email diferente.')
                    raise ValueError('Email já existe')
                
                # Verificar se email já existe na tabela de usuários (membros convertidos)
                existing_user = User.objects.filter(email=email).first()
                if existing_user:
                    # Em vez de usar messages, vamos passar informações para o template para mostrar um alerta JS
                    try:
                        existing_member = Member.objects.filter(user=existing_user).first()
                        if existing_member:
                            # Não bloquear, mas adicionar script para mostrar alerta
                            email_warning = {
                                'type': 'member_conflict',
                                'email': email,
                                'member_name': existing_member.name,
                                'message': f'O email "{email}" já pertence ao membro "{existing_member.name}". Verifique se são a mesma pessoa.'
                            }
                        else:
                            email_warning = {
                                'type': 'user_conflict', 
                                'email': email,
                                'message': f'O email "{email}" já está registrado no sistema como usuário. Verifique possíveis duplicatas.'
                            }
                    except Exception as member_check_error:
                        print(f"Erro ao verificar membro existente: {member_check_error}")
                        email_warning = {
                            'type': 'general_conflict',
                            'email': email,
                            'message': f'O email "{email}" já está registrado no sistema. Verifique possíveis duplicatas.'
                        }
                else:
                    email_warning = None
            
            # Verificar se phone já existe (apenas aviso, não bloqueia)
            if phone:
                # Normalizar telefone removendo caracteres especiais para comparação
                phone_numbers = phone.replace('(', '').replace(')', '').replace('-', '').replace(' ', '')
                existing_phone_visitor = Visitor.objects.filter(
                    phone__isnull=False
                ).exclude(id=visitor.id if visitor else None)
                
                for existing in existing_phone_visitor:
                    if existing.phone:
                        existing_phone_clean = existing.phone.replace('(', '').replace(')', '').replace('-', '').replace(' ', '')
                        if existing_phone_clean == phone_numbers:
                            messages.warning(request, f'O telefone "{phone}" já está cadastrado para o visitante "{existing.name}". Verifique se são a mesma pessoa.')
                            break
            
            # Get related objects
            neighborhood = None
            if neighborhood_id:
                neighborhood = Neighborhood.objects.get(id=neighborhood_id)
            
            if visitor:
                # Update existing visitor
                old_decision = visitor.decision_for_jesus
                old_conversion = visitor.conversion
                
                visitor.name = name
                visitor.email = email
                visitor.phone = phone
                visitor.address = address
                visitor.visit_date = visit_date
                visitor.gender = gender
                visitor.neighborhood = neighborhood
                visitor.decision_for_jesus = decision_for_jesus
                visitor.conversion = conversion
                visitor.prayer_request = prayer_request
                visitor.profile_notes = profile_notes
                
                try:
                    visitor.save()
                    print(f"Visitante {visitor.id} salvo com sucesso")
                except Exception as save_error:
                    print(f"Erro ao salvar visitante: {save_error}")
                    if 'unique constraint' in str(save_error).lower() or 'duplicate key' in str(save_error).lower():
                        if 'email' in str(save_error).lower():
                            messages.error(request, f'Erro: O email "{email}" já está em uso. Por favor, use um email diferente.')
                        else:
                            messages.error(request, 'Erro: Dados duplicados detectados. Verifique se este visitante já existe.')
                    else:
                        messages.error(request, f'Erro ao salvar visitante: {str(save_error)}')
                    raise save_error
                
                # Verificar se deve converter para membro (mudança de status)
                should_convert_now = should_convert_visitor_to_member(visitor)
                was_convertible_before = old_decision or old_conversion
                
                print(f"=== DEBUG CONVERSÃO ===")
                print(f"Visitante ID: {visitor.id}")
                print(f"should_convert_now: {should_convert_now}")
                print(f"was_convertible_before: {was_convertible_before}")
                print(f"decision_for_jesus: {visitor.decision_for_jesus}")
                print(f"conversion: {visitor.conversion}")
                print(f"profile_notes contém [CONVERTIDO]: {'[CONVERTIDO]' in (visitor.profile_notes or '')}")
                print("=======================")
                
                if should_convert_now:
                    if not was_convertible_before:
                        # Nova conversão
                        print("Tentando converter visitante (nova conversão)...")
                        member = convert_visitor_to_member(visitor)
                        if member:
                            messages.success(request, f'✅ Visitante atualizado e convertido para membro com sucesso! (Tipo: {member.conversion})')
                        else:
                            messages.warning(request, 'Visitante atualizado, mas houve erro na conversão para membro.')
                    else:
                        # Verificar se já foi convertido anteriormente
                        already_converted = '[CONVERTIDO]' in (visitor.profile_notes or '')
                        if not already_converted:
                            print("Tentando converter visitante (status existia mas não foi convertido)...")
                            member = convert_visitor_to_member(visitor)
                            if member:
                                messages.success(request, f'✅ Visitante atualizado e convertido para membro com sucesso! (Tipo: {member.conversion})')
                            else:
                                messages.warning(request, 'Visitante atualizado, mas houve erro na conversão para membro.')
                        else:
                            # Já era convertível e já foi convertido
                            messages.success(request, 'Visitante atualizado com sucesso! (Já era convertido anteriormente)')
                else:
                    messages.success(request, 'Visitante atualizado com sucesso!')
            else:
                # Create new visitor
                try:
                    visitor = Visitor.objects.create(
                        name=name,
                        email=email,
                        phone=phone,
                        address=address,
                        visit_date=visit_date,
                        gender=gender,
                        neighborhood=neighborhood,
                        decision_for_jesus=decision_for_jesus,
                        conversion=conversion,
                        prayer_request=prayer_request,
                        profile_notes=profile_notes
                    )
                    print(f"Novo visitante criado com ID: {visitor.id}")
                except Exception as create_error:
                    print(f"Erro ao criar visitante: {create_error}")
                    if 'unique constraint' in str(create_error).lower() or 'duplicate key' in str(create_error).lower():
                        if 'email' in str(create_error).lower():
                            messages.error(request, f'Erro: O email "{email}" já está em uso. Por favor, use um email diferente.')
                        else:
                            messages.error(request, 'Erro: Dados duplicados detectados. Verifique se este visitante já existe.')
                    else:
                        messages.error(request, f'Erro ao criar visitante: {create_error}')
                    raise create_error
                
                # Verificar se deve converter para membro
                if should_convert_visitor_to_member(visitor):
                    member = convert_visitor_to_member(visitor)
                    if member:
                        messages.success(request, f'Visitante criado e convertido para membro com sucesso! (Tipo: {member.conversion})')
                    else:
                        messages.warning(request, 'Visitante criado, mas houve erro na conversão para membro.')
                else:
                    messages.success(request, 'Visitante criado com sucesso!')
            
            return redirect('admin_visitors_list')
            
        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            print(f"Erro detalhado ao salvar visitante: {error_detail}")
            messages.error(request, f'Erro ao salvar visitante: {str(e)}')
            
    # Get data for form (garantir que sempre tenha dados para o template)
    neighborhoods = Neighborhood.objects.all().order_by('name')
    
    # Converter email_warning para JSON se existir
    email_warning_json = None
    if 'email_warning' in locals() and email_warning:
        import json
        email_warning_json = json.dumps(email_warning)
    
    context = {
        'visitor': visitor,
        'neighborhoods': neighborhoods,
        'email_warning': email_warning_json,  # Passar como JSON
    }
    
    return render(request, 'admin_panel/visitors/edit.html', context)


@user_passes_test(has_module_permission('events'))
def event_edit_view(request, event_id=None):
    """Criar ou editar evento"""
    event = None
    if event_id:
        event = get_object_or_404(Event, id=event_id)
    
    # Limpar mensagens antigas em GET requests para evitar mensagens persistentes
    if request.method == 'GET':
        storage = messages.get_messages(request)
        storage.used = True
    
    if request.method == 'POST':
        try:
            # Get form data
            title = request.POST.get('title')
            slug = request.POST.get('slug')
            description = request.POST.get('description')
            
            # Recorrência
            is_recurring = request.POST.get('is_recurring') == 'on'
            
            # Dependendo se é recorrente ou não, a data é processada de forma diferente
            if is_recurring:
                # Para eventos recorrentes, usamos o dia da semana
                weekday = int(request.POST.get('weekday', 0))  # Default para Segunda-feira (0)
                
                # Encontrar a próxima data que corresponde ao dia da semana selecionado
                today = timezone.now().date()
                days_ahead = weekday - today.weekday()
                if days_ahead < 0:  # Se o dia da semana já passou, vá para a próxima semana
                    days_ahead += 7
                event_date = today + timezone.timedelta(days=days_ahead)
                
                # Eventos recorrentes são sempre visíveis (definir datas bem distantes)
                display_start = timezone.now().date() - timezone.timedelta(days=1)  # Ontem
                display_end = timezone.now().date() + timezone.timedelta(days=3650)  # +10 anos
            else:
                # Para eventos normais, usamos a data específica
                event_date = request.POST.get('event_date')
                display_start = request.POST.get('display_start') or event_date
                display_end = request.POST.get('display_end') or event_date
            
            event_time = request.POST.get('event_time') or None
            location = request.POST.get('location') or None
            link_more_info = request.POST.get('link_more_info') or None
            link_type = request.POST.get('link_type') or 'more_info'
            
            # Para eventos não recorrentes, usamos as datas de exibição do formulário
            if not is_recurring:
                display_start = request.POST.get('display_start') or event_date
                display_end = request.POST.get('display_end') or event_date
            # Para eventos recorrentes, as datas são definidas automaticamente (já configuradas acima)
            
            # Outras informações de recorrência
            recurrence_pattern = request.POST.get('recurrence_pattern') if is_recurring else None
            # Não precisamos mais do campo recurrence_description para eventos recorrentes
            recurrence_description = None
            
            # Ensure slug is unique
            if not slug:
                slug = slugify(title)
            
            # Check for duplicate slug
            existing_event = Event.objects.filter(slug=slug)
            if event:
                existing_event = existing_event.exclude(id=event.id)
            
            if existing_event.exists():
                slug = f"{slug}-{timezone.now().strftime('%Y%m%d')}"
            
            if event:
                # Update existing event
                event.title = title
                event.slug = slug
                event.description = description
                event.event_date = event_date
                event.event_time = event_time
                event.location = location
                event.link_more_info = link_more_info
                event.link_type = link_type
                event.display_start = display_start
                event.display_end = display_end
                
                # Recorrência
                event.is_recurring = is_recurring
                event.recurrence_pattern = recurrence_pattern
                event.recurrence_description = recurrence_description
                
                # Se for evento recorrente, atualize a data com base no dia da semana selecionado
                if is_recurring and event_date:
                    event.event_date = event_date  # Já calculamos a data correta acima
                
                # Handle banner
                if 'banner' in request.FILES:
                    event.banner = request.FILES['banner']
                    event.save()
                
                event.save()
                messages.success(request, 'Evento atualizado com sucesso!')
            else:
                # Create new event
                event = Event.objects.create(
                    title=title,
                    slug=slug,
                    description=description,
                    event_date=event_date,
                    event_time=event_time,
                    location=location,
                    link_more_info=link_more_info,
                    link_type=link_type,
                    display_start=display_start,
                    display_end=display_end,
                    is_recurring=is_recurring,
                    recurrence_pattern=recurrence_pattern,
                    recurrence_description=recurrence_description
                )
                
                # Handle banner
                if 'banner' in request.FILES:
                    event.banner = request.FILES['banner']
                    event.save()
                
                messages.success(request, 'Evento criado com sucesso!')
            
            return redirect('admin_events_list')
            
        except Exception as e:
            messages.error(request, f'Erro ao salvar evento: {str(e)}')
    
    context = {
        'event': event,
    }
    
    return render(request, 'admin_panel/events/edit.html', context)


@user_passes_test(is_admin)
def ministry_edit_view(request, ministry_id=None):
    """View para criar/editar ministérios"""
    ministry = None
    if ministry_id:
        ministry = get_object_or_404(Ministry, id=ministry_id)
    
    # Limpar mensagens antigas em GET requests para evitar mensagens persistentes
    if request.method == 'GET':
        storage = messages.get_messages(request)
        storage.used = True
    
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            description = request.POST.get('description', '').strip()
            
            if not name:
                messages.error(request, 'Nome do ministério é obrigatório.')
                return render(request, 'admin_panel/ministries/edit.html', {'ministry': ministry})
            
            if ministry:
                # Update existing ministry
                ministry.name = name
                ministry.description = description
                ministry.save()
                messages.success(request, 'Ministério atualizado com sucesso!')
            else:
                # Create new ministry
                ministry = Ministry.objects.create(
                    name=name,
                    description=description
                )
                messages.success(request, 'Ministério criado com sucesso!')
            
            return redirect('admin_ministries_list')
            
        except Exception as e:
            messages.error(request, f'Erro ao salvar ministério: {str(e)}')
    
    context = {
        'ministry': ministry,
    }
    
    return render(request, 'admin_panel/ministries/edit.html', context)


@user_passes_test(is_admin)
def neighborhood_edit_view(request, neighborhood_id=None):
    """View para criar/editar bairros"""
    neighborhood = None
    if neighborhood_id:
        neighborhood = get_object_or_404(Neighborhood, id=neighborhood_id)
    
    # Limpar mensagens antigas em GET requests para evitar mensagens persistentes
    if request.method == 'GET':
        storage = messages.get_messages(request)
        storage.used = True
    
    # Get all neighborhoods except the current one (to avoid circular references)
    parent_neighborhoods = Neighborhood.objects.all()
    if neighborhood:
        parent_neighborhoods = parent_neighborhoods.exclude(id=neighborhood.id)
    
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            parent_id = request.POST.get('parent')
            
            if not name:
                messages.error(request, 'Nome do bairro é obrigatório.')
                return render(request, 'admin_panel/neighborhoods/edit.html', {
                    'neighborhood': neighborhood,
                    'parent_neighborhoods': parent_neighborhoods
                })
            
            parent = None
            if parent_id:
                parent = get_object_or_404(Neighborhood, id=parent_id)
            
            if neighborhood:
                # Update existing neighborhood
                neighborhood.name = name
                neighborhood.parent = parent
                neighborhood.save()
                messages.success(request, 'Bairro atualizado com sucesso!')
            else:
                # Create new neighborhood
                neighborhood = Neighborhood.objects.create(
                    name=name,
                    parent=parent
                )
                messages.success(request, 'Bairro criado com sucesso!')
            
            return redirect('admin_neighborhoods_list')
            
        except Exception as e:
            messages.error(request, f'Erro ao salvar bairro: {str(e)}')
    
    context = {
        'neighborhood': neighborhood,
        'parent_neighborhoods': parent_neighborhoods,
    }
    
    return render(request, 'admin_panel/neighborhoods/edit.html', context)


def should_convert_visitor_to_member(visitor):
    """Verifica se um visitante deve ser convertido para membro"""
    # Converter se:
    # 1. Fez decisão por Jesus OU
    # 2. Tem tipo de conversão definido (new_convert, reconciled, from_another_church)
    return visitor.decision_for_jesus or visitor.conversion

def convert_visitor_to_member(visitor):
    """Converte um visitante em membro quando ele faz decisão por Jesus ou tem conversão definida"""
    try:
        print(f"Iniciando conversão do visitante {visitor.id}: {visitor.name}")
        
        # Verificar se já existe um membro com o mesmo email/nome para evitar duplicatas
        existing_member = None
        if visitor.email:
            existing_member = Member.objects.filter(user__email=visitor.email).first()
            if existing_member:
                print(f"Membro já existe com email {visitor.email}")
                return existing_member
        
        # Verificar se visitante já foi convertido
        if visitor.profile_notes and '[CONVERTIDO]' in visitor.profile_notes:
            print(f"Visitante {visitor.id} já foi convertido anteriormente")
            # Tentar encontrar o membro existente
            if visitor.email:
                existing_member = Member.objects.filter(user__email=visitor.email).first()
                return existing_member
        
        # Criar usuário para o membro
        email = visitor.email
        if not email:
            # Gerar email temporário se não tiver
            email = f"membro_{uuid.uuid4().hex[:8]}@autogerado.com"
        
        print(f"Criando usuário com email: {email}")
        
        # Verificar se já existe usuário com este email
        existing_user = User.objects.filter(email=email).first()
        if existing_user:
            print(f"Usuário já existe com email {email}, reutilizando...")
            user = existing_user
            
            # Verificar se já tem membro associado
            existing_member = Member.objects.filter(user=existing_user).first()
            if existing_member:
                print(f"Membro já existe para este usuário: {existing_member.name}")
                return existing_member
        else:
            try:
                # Criar user
                user = User.objects.create_user(
                    email=email,
                    password='123',  # Senha padrão
                    user_type='member'  # Tipo de usuário padrão
                )
                print(f"Usuário criado com sucesso: {email}")
            except Exception as e:
                print(f"Erro ao criar usuário: {e}")
                # Se falhar, tentar com email único
                email = f"membro_{uuid.uuid4().hex[:12]}@autogerado.com"
                user = User.objects.create_user(
                    email=email,
                    password='123',
                    user_type='member'
                )
                print(f"Usuário criado com email alternativo: {email}")
        
        # Determinar tipo de conversão baseado nos dados do visitante
        conversion_type = 'new_convert'  # Padrão
        if visitor.conversion:
            conversion_type = visitor.conversion
        elif visitor.decision_for_jesus:
            conversion_type = 'new_convert'
        
        print(f"Criando membro com tipo de conversão: {conversion_type}")
        
        # Garantir que visit_date seja um objeto de data
        visit_date_obj = visitor.visit_date
        if isinstance(visit_date_obj, str):
            from datetime import datetime
            visit_date_obj = datetime.strptime(visit_date_obj, '%Y-%m-%d').date()
        
        # Criar membro baseado nos dados do visitante
        member = Member.objects.create(
            user=user,
            name=visitor.name,
            phone=visitor.phone,
            address=visitor.address,
            gender=visitor.gender,
            neighborhood=visitor.neighborhood,
            conversion=conversion_type,
            conversion_date=visit_date_obj,  # Usar data da visita como conversão
            is_active=True,
            testimony=f"Convertido a partir de visita em {visit_date_obj.strftime('%d/%m/%Y')} - Tipo: {conversion_type}"
        )
        
        print(f"Membro criado com ID: {member.id}")
        
        # Adicionar nota sobre a conversão no visitante
        conversion_note = f"\n\n[CONVERTIDO] Em {timezone.now().strftime('%d/%m/%Y %H:%M')} - Convertido para membro ID: {member.id} - Tipo: {conversion_type}"
        visitor.profile_notes = (visitor.profile_notes or "") + conversion_note
        visitor.save()
        
        print(f"Conversão concluída com sucesso para visitante {visitor.id}")
        return member
        
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print(f"Erro detalhado ao converter visitante {visitor.id} para membro: {error_detail}")
        return None

# === FOLLOW UP VIEWS ===

@login_required
@user_passes_test(has_module_permission('consolidation'))
def followup_list_view(request):
    """Listar follow-ups"""
    followups = FollowUp.objects.select_related('accompanied', 'responsible').order_by('-created_at')
    
    # Filtros
    search = request.GET.get('search', '').strip()
    responsible_filter = request.GET.get('responsible', '')
    status_filter = request.GET.get('status', '')
    
    if search:
        followups = followups.filter(
            Q(accompanied__name__icontains=search) |
            Q(responsible__name__icontains=search)
        )
    
    if responsible_filter:
        followups = followups.filter(responsible_id=responsible_filter)
    
    if status_filter == 'active':
        followups = followups.filter(end_date__isnull=True)
    elif status_filter == 'finished':
        followups = followups.filter(end_date__isnull=False)
    
    # Responsáveis para filtro
    responsibles = Member.objects.filter(
        is_active=True,
        is_available_to_consolidate=True
    ).order_by('name')
    
    return render(request, 'admin_panel/followups/list.html', {
        'followups': followups,
        'search': search,
        'responsible_filter': responsible_filter,
        'status_filter': status_filter,
        'responsibles': responsibles
    })

@login_required
@user_passes_test(has_module_permission('consolidation'))
def followup_edit_view(request, followup_id=None):
    """Criar ou editar follow-up"""
    followup = None
    if followup_id:
        followup = get_object_or_404(FollowUp, id=followup_id)
    
    if request.method == 'POST':
        form = FollowUpForm(request.POST, instance=followup)
        if form.is_valid():
            followup = form.save()
            messages.success(request, 'Follow-up salvo com sucesso!')
            return redirect('admin_followups_list')
        else:
            messages.error(request, 'Erro ao salvar follow-up. Verifique os dados.')
    else:
        form = FollowUpForm(instance=followup)
    
    # Buscar templates com suas informações para o JavaScript
    templates_data = []
    for template in FollowUpTemplate.objects.all():
        templates_data.append({
            'id': template.id,
            'name': template.name,
            'description': template.description or 'Sem descrição',
            'steps_count': template.steps.count()
        })
    
    return render(request, 'admin_panel/followups/edit.html', {
        'form': form,
        'followup': followup,
        'templates_data': templates_data
    })

@login_required
@user_passes_test(has_module_permission('consolidation'))
def followup_delete_view(request, followup_id):
    """Deletar follow-up"""
    followup = get_object_or_404(FollowUp, id=followup_id)
    
    if request.method == 'POST':
        followup.delete()
        messages.success(request, 'Follow-up excluído com sucesso!')
        return redirect('admin_followups_list')
    
    return render(request, 'admin_panel/followups/delete.html', {
        'followup': followup
    })

@login_required
@user_passes_test(has_module_permission('consolidation'))
def followup_report_view(request, followup_id):
    """Adicionar relatório ao follow-up"""
    followup = get_object_or_404(FollowUp, id=followup_id)
    
    if request.method == 'POST':
        form = FollowUpReportForm(request.POST)
        if form.is_valid():
            report = form.save(commit=False)
            report.followup = followup
            report.save()
            messages.success(request, 'Relatório adicionado com sucesso!')
            return redirect('admin_panel:followup_detail', followup_id=followup.id)
        else:
            messages.error(request, 'Erro ao salvar relatório. Verifique os dados.')
    else:
        form = FollowUpReportForm()
    
    return render(request, 'admin_panel/followups/report.html', {
        'form': form,
        'followup': followup
    })

@login_required
@user_passes_test(has_module_permission('consolidation'))
def followup_detail_view(request, followup_id):
    """Detalhes do follow-up com relatórios"""
    followup = get_object_or_404(FollowUp, id=followup_id)
    reports = FollowUpReport.objects.filter(followup=followup).order_by('-date')
    
    return render(request, 'admin_panel/followups/detail.html', {
        'followup': followup,
        'reports': reports
    })

@login_required
def profile_view(request):
    user = request.user
    if request.method == 'POST':
        form = UserProfileForm(request.POST, request.FILES, instance=user)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, user)  # Mantém o usuário logado após alteração de senha
            messages.success(request, 'Perfil atualizado com sucesso!')
            return redirect('admin_profile')
    else:
        form = UserProfileForm(instance=user)
    return render(request, 'admin_panel/profile.html', {'form': form, 'user': user})

# Canteen Views
@user_passes_test(has_module_permission('canteen'))
def canteen_list_view(request):
    """Lista todos os fiados registrados na cantina"""
    search = request.GET.get('search', '')
    status_filter = request.GET.get('status', '')
    
    debtors = CanteenDebtor.objects.all()
    
    # Filtrar por nome, se tiver busca
    if search:
        debtors = debtors.filter(
            Q(name__icontains=search) | 
            Q(phone__icontains=search) |
            Q(description__icontains=search)
        )
    
    # Filtrar por status de pagamento
    if status_filter:
        if status_filter == 'paid':
            debtors = debtors.filter(paid=True)
        elif status_filter == 'unpaid':
            debtors = debtors.filter(paid=False)
    
    # Ordenar por data de compra mais recente primeiro
    debtors = debtors.order_by('-purchase_date')
    
    # Paginação
    paginator = Paginator(debtors, 10)
    page_number = request.GET.get('page', 1)
    debtors_page = paginator.get_page(page_number)
    
    context = {
        'debtors': debtors_page,
        'search': search,
        'status_filter': status_filter,
        # Estatísticas para o resumo
        'total_debtors': CanteenDebtor.objects.count(),
        'total_unpaid': CanteenDebtor.objects.filter(paid=False).count(),
        'total_amount_unpaid': CanteenDebtor.objects.filter(paid=False).aggregate(total=Sum('amount'))['total'] or 0,
        'total_amount_paid': CanteenDebtor.objects.filter(paid=True).aggregate(total=Sum('amount'))['total'] or 0,
        'avg_debt_amount': CanteenDebtor.objects.aggregate(avg=Avg('amount'))['avg'] or 0,
        'recent_payments': CanteenDebtor.objects.filter(
            paid=True, 
            paid_date__gte=timezone.now() - timedelta(days=30)
        ).count(),
    }
    
    return render(request, 'admin_panel/cantina/list.html', context)

@user_passes_test(has_module_permission('canteen'))
def canteen_edit_view(request, debtor_id=None):
    """Cria ou edita um registro de fiado da cantina"""
    debtor = None
    if debtor_id:
        debtor = get_object_or_404(CanteenDebtor, id=debtor_id)
    
    if request.method == 'POST':
        form = CanteenDebtorForm(request.POST, instance=debtor)
        if form.is_valid():
            form.save()
            action = 'atualizado' if debtor_id else 'criado'
            messages.success(request, f'Registro de fiado {action} com sucesso!')
            return redirect('admin_cantina_list')
    else:
        form = CanteenDebtorForm(instance=debtor)
    
    context = {
        'form': form,
        'debtor': debtor,
        'is_edit': debtor_id is not None
    }
    
    return render(request, 'admin_panel/cantina/edit.html', context)

@user_passes_test(is_admin)
def canteen_detail_view(request, debtor_id):
    """Exibe os detalhes de um registro de fiado da cantina"""
    debtor = get_object_or_404(CanteenDebtor, id=debtor_id)
    
    context = {
        'debtor': debtor
    }
    
    return render(request, 'admin_panel/cantina/detail.html', context)

@user_passes_test(is_admin)
def canteen_delete_view(request, debtor_id):
    """Exclui um registro de fiado da cantina"""
    debtor = get_object_or_404(CanteenDebtor, id=debtor_id)
    
    if request.method == 'POST':
        debtor.delete()
        messages.success(request, 'Registro de fiado excluído com sucesso!')
        return redirect('admin_cantina_list')
    
    context = {
        'debtor': debtor
    }
    
    return render(request, 'admin_panel/cantina/delete.html', context)

@user_passes_test(is_admin)
def canteen_toggle_paid(request, debtor_id):
    """Toggle do status de pagamento de um fiado"""
    if request.method == 'POST':
        debtor = get_object_or_404(CanteenDebtor, id=debtor_id)
        debtor.paid = not debtor.paid
        
        if debtor.paid:
            debtor.paid_date = timezone.now().date()
        else:
            debtor.paid_date = None
            
        debtor.save()
        
        status = 'pago' if debtor.paid else 'não pago'
        messages.success(request, f'Status alterado para {status} com sucesso!')
    
    return redirect('admin_cantina_list')

@csrf_exempt
def canteen_api(request):
    """API para operações CRUD da cantina via AJAX"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido'}, status=405)
    
    try:
        data = json.loads(request.body)
        
        # Se tem ID, é uma edição
        if 'id' in data:
            debtor = get_object_or_404(CanteenDebtor, id=data['id'])
            
            # Atualizar campos
            for field in ['name', 'phone', 'purchase_date', 'amount', 'description', 'paid', 'notes']:
                if field in data:
                    setattr(debtor, field, data[field])
            
            # Tratar paid_date
            if 'paid' in data:
                debtor.paid = data['paid']
                if debtor.paid:
                    debtor.paid_date = timezone.now().date()
                else:
                    debtor.paid_date = None
            
            debtor.save()
            message = 'Registro atualizado com sucesso!'
        else:
            # Criar novo registro
            debtor = CanteenDebtor.objects.create(
                name=data.get('name'),
                phone=data.get('phone'),
                purchase_date=data.get('purchase_date', timezone.now().date()),
                amount=data.get('amount'),
                description=data.get('description', ''),
                paid=data.get('paid', False),
                notes=data.get('notes', '')
            )
            
            if debtor.paid:
                debtor.paid_date = timezone.now().date()
                debtor.save()
                
            message = 'Novo registro criado com sucesso!'
        
        return JsonResponse({
            'success': True,
            'message': message,
            'id': debtor.id
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })


# Views temporárias para Templates e Relatórios
@login_required
@user_passes_test(lambda u: u.user_type in ['admin', 'consolidation'] or u.is_superuser)
def templates_view(request):
    """Lista os templates de consolidação"""
    # Filtros
    search = request.GET.get('search', '')
    category_filter = request.GET.get('category', '')
    status_filter = request.GET.get('status', '')
    
    # Queryset base
    templates = FollowUpTemplate.objects.all()
    
    # Aplicar filtros
    if search:
        templates = templates.filter(
            Q(name__icontains=search) | Q(description__icontains=search)
        )
    
    # Anotar com informações estatísticas
    templates = templates.annotate(
        steps_count=Count('steps'),
        usage_count=Count('followup')
    ).order_by('-created_at')
    
    # Calcular estatísticas
    total_templates = FollowUpTemplate.objects.count()
    active_templates = templates.filter(followup__isnull=False).distinct().count()
    total_usage = FollowUp.objects.filter(template__isnull=False).count()
    
    context = {
        'templates': templates,
        'search': search,
        'category_filter': category_filter,
        'status_filter': status_filter,
        'stats': {
            'total_templates': total_templates,
            'active_templates': active_templates,
            'total_usage': total_usage
        }
    }
    return render(request, 'admin_panel/templates.html', context)


@login_required
@user_passes_test(lambda u: u.user_type in ['admin', 'consolidation'] or u.is_superuser)
def template_create_view(request):
    """Criar novo template de consolidação"""
    # Verificar se é duplicação de template existente
    duplicate_from_id = request.POST.get('duplicate_from')
    original_template = None
    
    if duplicate_from_id:
        try:
            original_template = FollowUpTemplate.objects.get(id=duplicate_from_id)
        except FollowUpTemplate.DoesNotExist:
            messages.error(request, 'Template para duplicação não encontrado.')
            return redirect('admin_templates')
    
    if request.method == 'POST':
        # Verificar se é duplicação primeiro
        if duplicate_from_id and original_template:
            # Para duplicação, usar os dados do POST para o nome
            form = FollowUpTemplateForm(request.POST)
            
            if form.is_valid():
                # Criar novo template com dados do formulário
                template = form.save(commit=False)
                template.description = original_template.description
                template.save()
                
                # Copiar etapas do template original
                for step in original_template.steps.all():
                    FollowUpTemplateStep.objects.create(
                        template=template,
                        period=step.period,
                        period_type=step.period_type,
                        title=step.title,
                        description=step.description
                    )
                
                messages.success(request, f'Template "{template.name}" criado com sucesso a partir de "{original_template.name}"!')
                return redirect('admin_template_detail', template_id=template.id)
            else:
                messages.error(request, 'Erro ao duplicar template. Verifique os dados informados.')
        else:
            # Criação normal
            form = FollowUpTemplateForm(request.POST)
            formset = FollowUpTemplateStepFormSetForCreate(request.POST)
            
            if form.is_valid() and formset.is_valid():
                template = form.save()
                formset.instance = template
                formset.save()
                
                messages.success(request, f'Template "{template.name}" criado com sucesso!')
                return redirect('admin_template_detail', template_id=template.id)
            else:
                messages.error(request, 'Erro ao criar template. Verifique os dados informados.')
    else:
        # Preparar formulário inicial
        if original_template:
            form = FollowUpTemplateForm(initial={
                'name': f'Cópia de {original_template.name}',
                'description': original_template.description
            })
            # Criar formset vazio para duplicação (etapas serão copiadas no POST)
            formset = FollowUpTemplateStepFormSetForCreate()
        else:
            form = FollowUpTemplateForm()
            formset = FollowUpTemplateStepFormSetForCreate()
    
    context = {
        'form': form,
        'formset': formset,
        'title': f'Duplicar Template: {original_template.name}' if original_template else 'Novo Template de Consolidação',
        'original_template': original_template
    }
    return render(request, 'admin_panel/template_form.html', context)


@login_required
@user_passes_test(lambda u: u.user_type in ['admin', 'consolidation'] or u.is_superuser)
def template_edit_view(request, template_id):
    """Editar template de consolidação"""
    template = get_object_or_404(FollowUpTemplate, id=template_id)
    
    if request.method == 'POST':
        form = FollowUpTemplateForm(request.POST, instance=template)
        formset = FollowUpTemplateStepFormSet(request.POST, instance=template)
        
        if form.is_valid() and formset.is_valid():
            template = form.save()
            formset.save()
            
            messages.success(request, f'Template "{template.name}" atualizado com sucesso!')
            return redirect('admin_template_detail', template_id=template.id)
        else:
            messages.error(request, 'Erro ao atualizar template. Verifique os dados informados.')
    else:
        form = FollowUpTemplateForm(instance=template)
        formset = FollowUpTemplateStepFormSet(instance=template)
    
    context = {
        'form': form,
        'formset': formset,
        'template': template,
        'title': f'Editar Template: {template.name}'
    }
    return render(request, 'admin_panel/template_form.html', context)


@login_required
@user_passes_test(lambda u: u.user_type in ['admin', 'consolidation'] or u.is_superuser)
def template_detail_view(request, template_id):
    """Visualizar detalhes do template"""
    template = get_object_or_404(FollowUpTemplate, id=template_id)
    steps = template.steps.all().order_by('week')
    followups_using = FollowUp.objects.filter(template=template)
    
    context = {
        'template': template,
        'steps': steps,
        'followups_using': followups_using,
        'usage_count': followups_using.count()
    }
    return render(request, 'admin_panel/template_detail.html', context)


@login_required
@user_passes_test(lambda u: u.user_type in ['admin', 'consolidation'] or u.is_superuser)
def template_delete_view(request, template_id):
    """Excluir template de consolidação"""
    template = get_object_or_404(FollowUpTemplate, id=template_id)
    
    if request.method == 'POST':
        template_name = template.name
        template.delete()
        messages.success(request, f'Template "{template_name}" excluído com sucesso!')
        return redirect('admin_templates')
    
    followups_using = FollowUp.objects.filter(template=template)
    
    context = {
        'template': template,
        'followups_using': followups_using
    }
    return render(request, 'admin_panel/template_delete.html', context)


@login_required
@user_passes_test(lambda u: u.user_type in ['admin', 'consolidation'] or u.is_superuser)
def reports_view(request):
    """Relatórios de consolidação com dados reais"""
    
    # Estatísticas gerais
    total_followups = FollowUp.objects.count()
    active_followups = FollowUp.objects.count()  # Todos são ativos por padrão
    completed_followups = 0
    overdue_followups = 0
    
    # Calcular estatísticas baseadas nas propriedades
    if total_followups > 0:
        for followup in FollowUp.objects.all():
            if followup.is_completed:
                completed_followups += 1
            elif followup.is_overdue:
                overdue_followups += 1
        
        # Recalcular ativos (total - concluídos)
        active_followups = total_followups - completed_followups
    
    # Taxa de conclusão
    completion_rate = 0
    if total_followups > 0:
        completion_rate = (completed_followups / total_followups) * 100
    
    # Consolidações recentes (últimas 10)
    recent_followups = FollowUp.objects.select_related(
        'accompanied', 'responsible', 'template'
    ).order_by('-created_at')[:10]
    
    # Estatísticas por template
    template_stats = []
    templates = FollowUpTemplate.objects.all()
    
    for template in templates:
        template_followups = FollowUp.objects.filter(template=template)
        if template_followups.exists():
            # Calcular progresso médio
            total_progress = 0
            count = 0
            for followup in template_followups:
                total_progress += followup.progress_percentage
                count += 1
            
            avg_progress = total_progress / count if count > 0 else 0
            template_stats.append({
                'name': template.name,
                'progress': avg_progress,
                'total_followups': template_followups.count()
            })
    
    context = {
        'title': 'Relatórios de Consolidação',
        'total_followups': total_followups,
        'active_followups': active_followups,
        'completed_followups': completed_followups,
        'overdue_followups': overdue_followups,
        'completion_rate': completion_rate,
        'recent_followups': recent_followups,
        'template_stats': template_stats,
    }
    return render(request, 'admin_panel/reports.html', context)
