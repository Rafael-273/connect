import json
import uuid
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.text import slugify
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .mixins import AdminRequiredMixin, ConsolidationPermissionMixin, ModulePermissionMixin
from ..forms.canteen import CanteenDebtorForm
from ..forms.follow_up import FollowUpForm, FollowUpReportForm
from ..forms.template import (
    FollowUpTemplateForm, FollowUpTemplateStepFormSet,
    FollowUpTemplateStepFormSetForCreate,
)
from ..forms.user import UserProfileForm
from ..models.canteen import CanteenDebtor
from ..models.event import Event
from ..models.follow_up import FollowUp, FollowUpReport, FollowUpTemplate, FollowUpTemplateStep
from ..models.member import Member
from ..models.ministry import Ministry
from ..models.neighborhood import Neighborhood
from ..models.visitor import Visitor

User = get_user_model()


# ---------------------------------------------------------------------------
# Utility helpers (not views)
# ---------------------------------------------------------------------------

def should_convert_visitor_to_member(visitor):
    """Verifica se um visitante deve ser convertido para membro"""
    return visitor.decision_for_jesus or visitor.conversion


def convert_visitor_to_member(visitor):
    """Converte um visitante em membro quando ele faz decisão por Jesus ou tem conversão definida"""
    try:
        # Verificar se já existe um membro com o mesmo email para evitar duplicatas
        if visitor.email:
            existing_member = Member.objects.filter(user__email=visitor.email).first()
            if existing_member:
                return existing_member

        # Verificar se visitante já foi convertido
        if visitor.profile_notes and '[CONVERTIDO]' in visitor.profile_notes:
            if visitor.email:
                return Member.objects.filter(user__email=visitor.email).first()
            return None

        # Criar usuário para o membro
        email = visitor.email or f"membro_{uuid.uuid4().hex[:8]}@autogerado.com"

        existing_user = User.objects.filter(email=email).first()
        if existing_user:
            existing_member = Member.objects.filter(user=existing_user).first()
            if existing_member:
                return existing_member
            user = existing_user
        else:
            try:
                user = User.objects.create_user(
                    email=email, password='123', user_type='member',
                )
            except Exception:
                email = f"membro_{uuid.uuid4().hex[:12]}@autogerado.com"
                user = User.objects.create_user(
                    email=email, password='123', user_type='member',
                )

        conversion_type = visitor.conversion or 'new_convert'

        visit_date_obj = visitor.visit_date
        if isinstance(visit_date_obj, str):
            from datetime import datetime
            visit_date_obj = datetime.strptime(visit_date_obj, '%Y-%m-%d').date()

        member = Member.objects.create(
            user=user,
            name=visitor.name,
            phone=visitor.phone,
            address=visitor.address,
            gender=visitor.gender,
            neighborhood=visitor.neighborhood,
            conversion=conversion_type,
            conversion_date=visit_date_obj,
            is_active=True,
            testimony=f"Convertido a partir de visita em {visit_date_obj.strftime('%d/%m/%Y')} - Tipo: {conversion_type}",
        )

        conversion_note = (
            f"\n\n[CONVERTIDO] Em {timezone.now().strftime('%d/%m/%Y %H:%M')} "
            f"- Convertido para membro ID: {member.id} - Tipo: {conversion_type}"
        )
        visitor.profile_notes = (visitor.profile_notes or "") + conversion_note
        visitor.save()

        return member

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class DashboardView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Dashboard principal com estatísticas e métricas importantes"""

    def get(self, request):
        last_month = timezone.now() - timedelta(days=30)
        next_month = timezone.now() + timedelta(days=30)

        total_members = Member.objects.filter(is_active=True).count()
        total_visitors = Visitor.objects.count()
        total_events = Event.objects.count()
        total_ministries = Ministry.objects.count()

        recent_visitors = Visitor.objects.filter(visit_date__gte=last_month).count()
        upcoming_events = Event.objects.filter(
            event_date__gte=timezone.now().date(),
            event_date__lte=next_month.date(),
        ).count()

        recent_visitor_conversions = Visitor.objects.filter(
            visit_date__gte=last_month, decision_for_jesus=True,
        ).count()
        recent_member_conversions = Member.objects.filter(
            conversion_date__gte=last_month.date(), conversion='new_convert',
        ).count()
        recent_conversions = recent_visitor_conversions + recent_member_conversions

        # Gráfico de visitantes por mês (últimos 6 meses)
        visitors_by_month = []
        for i in range(6):
            month_start = (timezone.now() - timedelta(days=30 * i)).replace(day=1)
            month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            count = Visitor.objects.filter(
                visit_date__gte=month_start, visit_date__lte=month_end,
            ).count()
            visitors_by_month.append({'month': month_start.strftime('%b/%Y'), 'count': count})
        visitors_by_month.reverse()

        ministries_stats = Ministry.objects.annotate(
            member_count=Count('member'),
        ).order_by('-member_count')[:5]

        recent_visitors_list = Visitor.objects.order_by('-visit_date')[:5]
        recently_converted_members = Member.objects.filter(
            conversion='new_convert',
        ).order_by('-update_at')[:3]
        upcoming_events_list = Event.objects.filter(
            event_date__gte=timezone.now().date(),
        ).order_by('event_date')[:5]

        active_followups = FollowUp.objects.filter(is_active=True).count()
        recent_followups = FollowUp.objects.filter(
            created_at__gte=last_month,
        ).order_by('-created_at')[:5]

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
            'visitors_by_month_json': json.dumps(visitors_by_month),
            'ministries_stats': ministries_stats,
            'recent_visitors_list': recent_visitors_list,
            'recently_converted_members': recently_converted_members,
            'upcoming_events_list': upcoming_events_list,
            'active_followups': active_followups,
            'recent_followups': recent_followups,
        }
        return render(request, 'admin_panel/dashboard.html', context)


# ---------------------------------------------------------------------------
# List Views
# ---------------------------------------------------------------------------

class MembersListView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Lista de membros com filtros e busca"""

    def get(self, request):
        members = Member.objects.select_related('user', 'neighborhood', 'spouse').order_by('-id')

        search = request.GET.get('search', '')
        ministry_filter = request.GET.get('ministry', '')
        status_filter = request.GET.get('status', '')

        if search:
            members = members.filter(
                Q(name__icontains=search)
                | Q(user__email__icontains=search)
                | Q(phone__icontains=search)
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

        paginator = Paginator(members, 20)
        members = paginator.get_page(request.GET.get('page'))
        ministries = Ministry.objects.all()

        if 'member_success_message' in request.session:
            messages.success(request, request.session.pop('member_success_message'))

        return render(request, 'admin_panel/members/list.html', {
            'members': members,
            'ministries': ministries,
            'search': search,
            'ministry_filter': ministry_filter,
            'status_filter': status_filter,
        })


class VisitorsListView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Lista de visitantes com filtros e busca"""
    module_name = 'visitors'

    def get(self, request):
        visitors = Visitor.objects.select_related('neighborhood').order_by('-visit_date', '-id')

        search = request.GET.get('search', '')
        period_filter = request.GET.get('period', '')
        conversion_filter = request.GET.get('conversion', '')

        if search:
            visitors = visitors.filter(
                Q(name__icontains=search)
                | Q(email__icontains=search)
                | Q(phone__icontains=search)
            )
        if period_filter == '7_days':
            visitors = visitors.filter(visit_date__gte=timezone.now() - timedelta(days=7))
        elif period_filter == '30_days':
            visitors = visitors.filter(visit_date__gte=timezone.now() - timedelta(days=30))

        if conversion_filter == 'converted':
            visitors = visitors.filter(decision_for_jesus=True)
        elif conversion_filter == 'not_converted':
            visitors = visitors.filter(decision_for_jesus=False)

        paginator = Paginator(visitors, 20)
        visitors = paginator.get_page(request.GET.get('page'))

        return render(request, 'admin_panel/visitors/list.html', {
            'visitors': visitors,
            'search': search,
            'period_filter': period_filter,
            'conversion_filter': conversion_filter,
        })


class EventsListView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Lista de eventos com filtros e busca"""
    module_name = 'events'

    def get(self, request):
        events = Event.objects.all()

        search = request.GET.get('search', '')
        status_filter = request.GET.get('status', '')

        if search:
            events = events.filter(
                Q(title__icontains=search)
                | Q(description__icontains=search)
                | Q(location__icontains=search)
            )
        if status_filter:
            today = timezone.now().date()
            if status_filter == 'upcoming':
                events = events.filter(event_date__gte=today)
            elif status_filter == 'past':
                events = events.filter(event_date__lt=today)

        events = events.order_by('-event_date')
        paginator = Paginator(events, 20)
        events = paginator.get_page(request.GET.get('page'))

        return render(request, 'admin_panel/events/list.html', {
            'events': events,
            'search': search,
            'status_filter': status_filter,
        })


class MinistriesListView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Lista de ministérios"""

    def get(self, request):
        ministries = Ministry.objects.annotate(member_count=Count('member')).order_by('name')

        search = request.GET.get('search', '')
        if search:
            ministries = ministries.filter(name__icontains=search)

        paginator = Paginator(ministries, 20)
        ministries = paginator.get_page(request.GET.get('page'))

        return render(request, 'admin_panel/ministries/list.html', {
            'ministries': ministries,
            'search': search,
        })


class NeighborhoodsListView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Lista de bairros"""

    def get(self, request):
        neighborhoods = Neighborhood.objects.select_related('parent').order_by('name')

        search = request.GET.get('search', '')
        if search:
            neighborhoods = neighborhoods.filter(name__icontains=search)

        paginator = Paginator(neighborhoods, 20)
        neighborhoods = paginator.get_page(request.GET.get('page'))

        return render(request, 'admin_panel/neighborhoods/list.html', {
            'neighborhoods': neighborhoods,
            'search': search,
        })


# ---------------------------------------------------------------------------
# API Views (JSON endpoints)
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class ApiDeleteItemView(LoginRequiredMixin, AdminRequiredMixin, View):
    """API para deletar itens via AJAX"""

    MODEL_MAP = {
        'member': Member,
        'visitor': Visitor,
        'event': Event,
        'ministry': Ministry,
        'neighborhood': Neighborhood,
    }

    def post(self, request):
        try:
            data = json.loads(request.body)
            model_class = self.MODEL_MAP.get(data.get('model'))
            if not model_class:
                return JsonResponse({'success': False, 'error': 'Modelo inválido'})

            item = get_object_or_404(model_class, id=data.get('id'))
            item.delete()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})


@method_decorator(csrf_exempt, name='dispatch')
class MinistryCreateEditApiView(LoginRequiredMixin, AdminRequiredMixin, View):
    """API para criar/editar ministérios via AJAX"""

    def post(self, request):
        try:
            data = json.loads(request.body)
            ministry_id = data.get('id')
            name = data.get('name')
            description = data.get('description', '')

            if ministry_id:
                ministry = get_object_or_404(Ministry, id=ministry_id)
                ministry.name = name
                ministry.description = description
                ministry.save()
                return JsonResponse({'success': True, 'message': 'Ministério atualizado com sucesso!'})
            else:
                Ministry.objects.create(name=name, description=description)
                return JsonResponse({'success': True, 'message': 'Ministério criado com sucesso!'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})


@method_decorator(csrf_exempt, name='dispatch')
class NeighborhoodCreateEditApiView(LoginRequiredMixin, AdminRequiredMixin, View):
    """API para criar/editar bairros via AJAX"""

    def post(self, request):
        try:
            data = json.loads(request.body)
            neighborhood_id = data.get('id')
            name = data.get('name')
            parent_id = data.get('parent')

            parent = get_object_or_404(Neighborhood, id=parent_id) if parent_id else None

            if neighborhood_id:
                neighborhood = get_object_or_404(Neighborhood, id=neighborhood_id)
                neighborhood.name = name
                neighborhood.parent = parent
                neighborhood.save()
                return JsonResponse({'success': True, 'message': 'Bairro atualizado com sucesso!'})
            else:
                Neighborhood.objects.create(name=name, parent=parent)
                return JsonResponse({'success': True, 'message': 'Bairro criado com sucesso!'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})


class MemberDetailApiView(LoginRequiredMixin, AdminRequiredMixin, View):
    """API para obter detalhes de um membro"""

    def get(self, request, member_id):
        member = get_object_or_404(Member, id=member_id)
        return JsonResponse({
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
        })


class VisitorDetailApiView(LoginRequiredMixin, AdminRequiredMixin, View):
    """API para obter detalhes de um visitante"""

    def get(self, request, visitor_id):
        visitor = get_object_or_404(Visitor, id=visitor_id)
        return JsonResponse({
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
        })


# ---------------------------------------------------------------------------
# Edit / Create Views
# ---------------------------------------------------------------------------

class MemberEditView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Criar ou editar membro"""

    def _get_form_context(self, member=None):
        return {
            'member': member,
            'ministries': Ministry.objects.all().order_by('name'),
            'neighborhoods': Neighborhood.objects.all().order_by('name'),
            'all_members': Member.objects.all().order_by('name'),
        }

    def get(self, request, member_id=None):
        member = get_object_or_404(Member, id=member_id) if member_id else None
        # Limpar mensagens antigas para evitar mensagens persistentes
        storage = messages.get_messages(request)
        storage.used = True
        return render(request, 'admin_panel/members/edit.html', self._get_form_context(member))

    def post(self, request, member_id=None):
        member = get_object_or_404(Member, id=member_id) if member_id else None

        try:
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
            is_active = request.POST.get('is_active') == 'on'
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
            user_type = request.POST.get('user_type') or 'member'

            if not name:
                messages.error(request, 'Nome é obrigatório.')
                raise ValueError('Nome é obrigatório')

            ministries = list(Ministry.objects.filter(id__in=ministry_ids)) if ministry_ids else []
            neighborhood = Neighborhood.objects.get(id=neighborhood_id) if neighborhood_id else None
            spouse = Member.objects.get(id=spouse_id) if spouse_id else None

            if member:
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

                if email and member.user:
                    member.user.email = email
                    member.user.user_type = user_type
                    member.user.is_staff = user_type != 'member'
                    member.user.save()

                if 'profile_picture' in request.FILES:
                    member.profile_picture = request.FILES['profile_picture']

                member.save()
                member.ministry.set(ministries)
                request.session['member_success_message'] = 'Membro atualizado com sucesso!'
            else:
                if not email:
                    email = f"membro_{uuid.uuid4().hex[:8]}@autogerado.com"

                user = User.objects.create_user(
                    email=email, password='123',
                    user_type=user_type, is_staff=user_type != 'member',
                )

                member = Member.objects.create(
                    user=user, name=name, phone=phone, address=address,
                    birth_date=birth_date, gender=gender, marital_status=marital_status,
                    neighborhood=neighborhood, conversion=conversion,
                    conversion_date=conversion_date,
                    is_active=is_active if is_active is not None else True,
                    testimony=testimony, interests=interests, tags=tags,
                    spouse=spouse, personality_type=personality_type,
                    initial_challenges=initial_challenges,
                    available_days=available_days,
                    is_available_to_consolidate=is_available_to_consolidate,
                    is_available_to_disciple=is_available_to_disciple,
                    is_approver=is_approver,
                )
                member.ministry.set(ministries)

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

        return render(request, 'admin_panel/members/edit.html', self._get_form_context(member))


class VisitorEditView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Criar ou editar visitante"""
    module_name = 'visitors'

    def get(self, request, visitor_id=None):
        visitor = get_object_or_404(Visitor, id=visitor_id) if visitor_id else None
        storage = messages.get_messages(request)
        storage.used = True
        return render(request, 'admin_panel/visitors/edit.html', {
            'visitor': visitor,
            'neighborhoods': Neighborhood.objects.all().order_by('name'),
            'email_warning': None,
        })

    def post(self, request, visitor_id=None):
        visitor = get_object_or_404(Visitor, id=visitor_id) if visitor_id else None
        email_warning = None

        try:
            name = request.POST.get('name', '').strip()
            email = request.POST.get('email', '').strip() or None
            phone = request.POST.get('phone', '').strip() or None
            address = request.POST.get('address', '').strip() or None
            visit_date = request.POST.get('visit_date', '').strip()
            gender = request.POST.get('gender', '').strip() or None
            neighborhood_id = request.POST.get('neighborhood', '').strip() or None
            decision_for_jesus = request.POST.get('decision_for_jesus') in ('true', 'on')
            conversion = request.POST.get('conversion', '').strip() or None
            prayer_request = request.POST.get('prayer_request', '').strip() or None
            profile_notes = request.POST.get('profile_notes', '').strip() or None

            if not name:
                messages.error(request, 'Nome é obrigatório.')
                raise ValueError('Nome é obrigatório')

            if not visit_date:
                messages.error(request, 'Data da visita é obrigatória.')
                raise ValueError('Data da visita é obrigatória')

            from datetime import datetime
            try:
                datetime.strptime(visit_date, '%Y-%m-%d')
            except ValueError:
                messages.error(request, 'Formato de data inválido. Use AAAA-MM-DD.')
                raise ValueError('Formato de data inválido')

            # Validar email único para visitantes
            if email:
                existing_visitor = Visitor.objects.filter(email=email)
                if visitor:
                    existing_visitor = existing_visitor.exclude(id=visitor.id)
                if existing_visitor.exists():
                    messages.error(request, f'Já existe um visitante com o email "{email}". Por favor, use um email diferente.')
                    raise ValueError('Email já existe')

                existing_user = User.objects.filter(email=email).first()
                if existing_user:
                    try:
                        existing_member = Member.objects.filter(user=existing_user).first()
                        if existing_member:
                            email_warning = {
                                'type': 'member_conflict', 'email': email,
                                'member_name': existing_member.name,
                                'message': f'O email "{email}" já pertence ao membro "{existing_member.name}". Verifique se são a mesma pessoa.',
                            }
                        else:
                            email_warning = {
                                'type': 'user_conflict', 'email': email,
                                'message': f'O email "{email}" já está registrado no sistema como usuário. Verifique possíveis duplicatas.',
                            }
                    except Exception:
                        email_warning = {
                            'type': 'general_conflict', 'email': email,
                            'message': f'O email "{email}" já está registrado no sistema. Verifique possíveis duplicatas.',
                        }

            # Verificar telefone duplicado (aviso, não bloqueia)
            if phone:
                phone_clean = phone.replace('(', '').replace(')', '').replace('-', '').replace(' ', '')
                existing_phone_qs = Visitor.objects.filter(phone__isnull=False)
                if visitor:
                    existing_phone_qs = existing_phone_qs.exclude(id=visitor.id)
                for existing in existing_phone_qs:
                    if existing.phone:
                        existing_clean = existing.phone.replace('(', '').replace(')', '').replace('-', '').replace(' ', '')
                        if existing_clean == phone_clean:
                            messages.warning(request, f'O telefone "{phone}" já está cadastrado para o visitante "{existing.name}". Verifique se são a mesma pessoa.')
                            break

            neighborhood = Neighborhood.objects.get(id=neighborhood_id) if neighborhood_id else None

            if visitor:
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
                except Exception as save_error:
                    error_str = str(save_error).lower()
                    if 'unique constraint' in error_str or 'duplicate key' in error_str:
                        if 'email' in error_str:
                            messages.error(request, f'Erro: O email "{email}" já está em uso. Por favor, use um email diferente.')
                        else:
                            messages.error(request, 'Erro: Dados duplicados detectados. Verifique se este visitante já existe.')
                    else:
                        messages.error(request, f'Erro ao salvar visitante: {save_error}')
                    raise save_error

                should_convert_now = should_convert_visitor_to_member(visitor)
                was_convertible_before = old_decision or old_conversion

                if should_convert_now:
                    already_converted = '[CONVERTIDO]' in (visitor.profile_notes or '')
                    if not was_convertible_before or not already_converted:
                        member = convert_visitor_to_member(visitor)
                        if member:
                            messages.success(request, f'✅ Visitante atualizado e convertido para membro com sucesso! (Tipo: {member.conversion})')
                        else:
                            messages.warning(request, 'Visitante atualizado, mas houve erro na conversão para membro.')
                    else:
                        messages.success(request, 'Visitante atualizado com sucesso! (Já era convertido anteriormente)')
                else:
                    messages.success(request, 'Visitante atualizado com sucesso!')
            else:
                try:
                    visitor = Visitor.objects.create(
                        name=name, email=email, phone=phone, address=address,
                        visit_date=visit_date, gender=gender,
                        neighborhood=neighborhood,
                        decision_for_jesus=decision_for_jesus,
                        conversion=conversion, prayer_request=prayer_request,
                        profile_notes=profile_notes,
                    )
                except Exception as create_error:
                    error_str = str(create_error).lower()
                    if 'unique constraint' in error_str or 'duplicate key' in error_str:
                        if 'email' in error_str:
                            messages.error(request, f'Erro: O email "{email}" já está em uso. Por favor, use um email diferente.')
                        else:
                            messages.error(request, 'Erro: Dados duplicados detectados. Verifique se este visitante já existe.')
                    else:
                        messages.error(request, f'Erro ao criar visitante: {create_error}')
                    raise create_error

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
            messages.error(request, f'Erro ao salvar visitante: {e}')

        return render(request, 'admin_panel/visitors/edit.html', {
            'visitor': visitor,
            'neighborhoods': Neighborhood.objects.all().order_by('name'),
            'email_warning': json.dumps(email_warning) if email_warning else None,
        })


class EventEditView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Criar ou editar evento"""
    module_name = 'events'

    def get(self, request, event_id=None):
        event = get_object_or_404(Event, id=event_id) if event_id else None
        storage = messages.get_messages(request)
        storage.used = True
        return render(request, 'admin_panel/events/edit.html', {'event': event})

    def post(self, request, event_id=None):
        event = get_object_or_404(Event, id=event_id) if event_id else None

        try:
            title = request.POST.get('title')
            slug = request.POST.get('slug')
            description = request.POST.get('description')
            is_recurring = request.POST.get('is_recurring') == 'on'

            if is_recurring:
                weekday = int(request.POST.get('weekday', 0))
                today = timezone.now().date()
                days_ahead = weekday - today.weekday()
                if days_ahead < 0:
                    days_ahead += 7
                event_date = today + timezone.timedelta(days=days_ahead)
                display_start = timezone.now().date() - timezone.timedelta(days=1)
                display_end = timezone.now().date() + timezone.timedelta(days=3650)
            else:
                event_date = request.POST.get('event_date')
                display_start = request.POST.get('display_start') or event_date
                display_end = request.POST.get('display_end') or event_date

            event_time = request.POST.get('event_time') or None
            location = request.POST.get('location') or None
            link_more_info = request.POST.get('link_more_info') or None
            link_type = request.POST.get('link_type') or 'more_info'

            if not is_recurring:
                display_start = request.POST.get('display_start') or event_date
                display_end = request.POST.get('display_end') or event_date

            recurrence_pattern = request.POST.get('recurrence_pattern') if is_recurring else None
            recurrence_description = None

            if not slug:
                slug = slugify(title)

            existing_event = Event.objects.filter(slug=slug)
            if event:
                existing_event = existing_event.exclude(id=event.id)
            if existing_event.exists():
                slug = f"{slug}-{timezone.now().strftime('%Y%m%d')}"

            if event:
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
                event.is_recurring = is_recurring
                event.recurrence_pattern = recurrence_pattern
                event.recurrence_description = recurrence_description

                if is_recurring and event_date:
                    event.event_date = event_date

                if 'banner' in request.FILES:
                    event.banner = request.FILES['banner']
                    event.save()

                event.save()
                messages.success(request, 'Evento atualizado com sucesso!')
            else:
                event = Event.objects.create(
                    title=title, slug=slug, description=description,
                    event_date=event_date, event_time=event_time,
                    location=location, link_more_info=link_more_info,
                    link_type=link_type, display_start=display_start,
                    display_end=display_end, is_recurring=is_recurring,
                    recurrence_pattern=recurrence_pattern,
                    recurrence_description=recurrence_description,
                )

                if 'banner' in request.FILES:
                    event.banner = request.FILES['banner']
                    event.save()

                messages.success(request, 'Evento criado com sucesso!')

            return redirect('admin_events_list')

        except Exception as e:
            messages.error(request, f'Erro ao salvar evento: {e}')

        return render(request, 'admin_panel/events/edit.html', {'event': event})


class MinistryEditView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Criar ou editar ministério"""

    def get(self, request, ministry_id=None):
        ministry = get_object_or_404(Ministry, id=ministry_id) if ministry_id else None
        storage = messages.get_messages(request)
        storage.used = True
        return render(request, 'admin_panel/ministries/edit.html', {'ministry': ministry})

    def post(self, request, ministry_id=None):
        ministry = get_object_or_404(Ministry, id=ministry_id) if ministry_id else None

        try:
            name = request.POST.get('name', '').strip()
            description = request.POST.get('description', '').strip()

            if not name:
                messages.error(request, 'Nome do ministério é obrigatório.')
                return render(request, 'admin_panel/ministries/edit.html', {'ministry': ministry})

            if ministry:
                ministry.name = name
                ministry.description = description
                ministry.save()
                messages.success(request, 'Ministério atualizado com sucesso!')
            else:
                Ministry.objects.create(name=name, description=description)
                messages.success(request, 'Ministério criado com sucesso!')

            return redirect('admin_ministries_list')

        except Exception as e:
            messages.error(request, f'Erro ao salvar ministério: {e}')

        return render(request, 'admin_panel/ministries/edit.html', {'ministry': ministry})


class NeighborhoodEditView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Criar ou editar bairro"""

    def _get_parent_neighborhoods(self, neighborhood=None):
        qs = Neighborhood.objects.all()
        if neighborhood:
            qs = qs.exclude(id=neighborhood.id)
        return qs

    def get(self, request, neighborhood_id=None):
        neighborhood = get_object_or_404(Neighborhood, id=neighborhood_id) if neighborhood_id else None
        storage = messages.get_messages(request)
        storage.used = True
        return render(request, 'admin_panel/neighborhoods/edit.html', {
            'neighborhood': neighborhood,
            'parent_neighborhoods': self._get_parent_neighborhoods(neighborhood),
        })

    def post(self, request, neighborhood_id=None):
        neighborhood = get_object_or_404(Neighborhood, id=neighborhood_id) if neighborhood_id else None

        try:
            name = request.POST.get('name', '').strip()
            parent_id = request.POST.get('parent')

            if not name:
                messages.error(request, 'Nome do bairro é obrigatório.')
                return render(request, 'admin_panel/neighborhoods/edit.html', {
                    'neighborhood': neighborhood,
                    'parent_neighborhoods': self._get_parent_neighborhoods(neighborhood),
                })

            parent = get_object_or_404(Neighborhood, id=parent_id) if parent_id else None

            if neighborhood:
                neighborhood.name = name
                neighborhood.parent = parent
                neighborhood.save()
                messages.success(request, 'Bairro atualizado com sucesso!')
            else:
                Neighborhood.objects.create(name=name, parent=parent)
                messages.success(request, 'Bairro criado com sucesso!')

            return redirect('admin_neighborhoods_list')

        except Exception as e:
            messages.error(request, f'Erro ao salvar bairro: {e}')

        return render(request, 'admin_panel/neighborhoods/edit.html', {
            'neighborhood': neighborhood,
            'parent_neighborhoods': self._get_parent_neighborhoods(neighborhood),
        })


# ---------------------------------------------------------------------------
# Follow-up Views
# ---------------------------------------------------------------------------

class FollowUpListView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Listar follow-ups"""
    module_name = 'consolidation'

    def get(self, request):
        followups = FollowUp.objects.select_related('accompanied', 'responsible').order_by('-created_at')

        search = request.GET.get('search', '').strip()
        responsible_filter = request.GET.get('responsible', '')
        status_filter = request.GET.get('status', '')

        if search:
            followups = followups.filter(
                Q(accompanied__name__icontains=search)
                | Q(responsible__name__icontains=search)
            )
        if responsible_filter:
            followups = followups.filter(responsible_id=responsible_filter)
        if status_filter == 'active':
            followups = followups.filter(end_date__isnull=True)
        elif status_filter == 'finished':
            followups = followups.filter(end_date__isnull=False)

        responsibles = Member.objects.filter(
            is_active=True, is_available_to_consolidate=True,
        ).order_by('name')

        return render(request, 'admin_panel/followups/list.html', {
            'followups': followups,
            'search': search,
            'responsible_filter': responsible_filter,
            'status_filter': status_filter,
            'responsibles': responsibles,
        })


class FollowUpEditView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Criar ou editar follow-up"""
    module_name = 'consolidation'

    def get(self, request, followup_id=None):
        followup = get_object_or_404(FollowUp, id=followup_id) if followup_id else None
        form = FollowUpForm(instance=followup)
        templates_data = list(
            FollowUpTemplate.objects.annotate(
                steps_count_val=Count('steps'),
            ).values_list('id', 'name', 'description', 'steps_count_val')
        )
        templates_data = [
            {'id': t[0], 'name': t[1], 'description': t[2] or 'Sem descrição', 'steps_count': t[3]}
            for t in templates_data
        ]
        return render(request, 'admin_panel/followups/edit.html', {
            'form': form, 'followup': followup, 'templates_data': templates_data,
        })

    def post(self, request, followup_id=None):
        followup = get_object_or_404(FollowUp, id=followup_id) if followup_id else None
        form = FollowUpForm(request.POST, instance=followup)
        if form.is_valid():
            form.save()
            messages.success(request, 'Follow-up salvo com sucesso!')
            return redirect('admin_followups_list')

        messages.error(request, 'Erro ao salvar follow-up. Verifique os dados.')
        templates_data = [
            {'id': t.id, 'name': t.name, 'description': t.description or 'Sem descrição', 'steps_count': t.steps.count()}
            for t in FollowUpTemplate.objects.all()
        ]
        return render(request, 'admin_panel/followups/edit.html', {
            'form': form, 'followup': followup, 'templates_data': templates_data,
        })


class FollowUpDeleteView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Deletar follow-up"""
    module_name = 'consolidation'

    def get(self, request, followup_id):
        followup = get_object_or_404(FollowUp, id=followup_id)
        return render(request, 'admin_panel/followups/delete.html', {'followup': followup})

    def post(self, request, followup_id):
        followup = get_object_or_404(FollowUp, id=followup_id)
        try:
            followup.delete()

            if request.headers.get('Content-Type') == 'application/json':
                return JsonResponse({'success': True, 'message': 'Consolidação excluída com sucesso!'})

            messages.success(request, 'Follow-up excluído com sucesso!')
            return redirect('admin_followups_list')
        except Exception as e:
            if request.headers.get('Content-Type') == 'application/json':
                return JsonResponse({'success': False, 'error': str(e)}, status=400)

            messages.error(request, f'Erro ao excluir follow-up: {e}')
            return redirect('admin_followups_list')


class FollowUpReportView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Adicionar relatório ao follow-up"""
    module_name = 'consolidation'

    def get(self, request, followup_id):
        followup = get_object_or_404(FollowUp, id=followup_id)
        form = FollowUpReportForm()
        return render(request, 'admin_panel/followups/report.html', {'form': form, 'followup': followup})

    def post(self, request, followup_id):
        followup = get_object_or_404(FollowUp, id=followup_id)
        form = FollowUpReportForm(request.POST)
        if form.is_valid():
            report = form.save(commit=False)
            report.followup = followup
            report.save()
            messages.success(request, 'Relatório adicionado com sucesso!')
            return redirect('admin_followup_detail', followup_id=followup.id)

        messages.error(request, 'Erro ao salvar relatório. Verifique os dados.')
        return render(request, 'admin_panel/followups/report.html', {'form': form, 'followup': followup})


class FollowUpDetailView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Detalhes do follow-up com relatórios"""
    module_name = 'consolidation'

    def get(self, request, followup_id):
        followup = get_object_or_404(FollowUp, id=followup_id)
        reports = FollowUpReport.objects.filter(followup=followup).order_by('-date')
        return render(request, 'admin_panel/followups/detail.html', {
            'followup': followup, 'reports': reports,
        })


# ---------------------------------------------------------------------------
# Profile View
# ---------------------------------------------------------------------------

class ProfileView(LoginRequiredMixin, View):
    """Perfil do usuário admin"""

    def get(self, request):
        form = UserProfileForm(instance=request.user)
        return render(request, 'admin_panel/profile.html', {'form': form, 'user': request.user})

    def post(self, request):
        form = UserProfileForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, request.user)
            messages.success(request, 'Perfil atualizado com sucesso!')
            return redirect('admin_profile')
        return render(request, 'admin_panel/profile.html', {'form': form, 'user': request.user})


# ---------------------------------------------------------------------------
# Canteen Views
# ---------------------------------------------------------------------------

class CanteenListView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Lista todos os fiados registrados na cantina"""
    module_name = 'canteen'

    def get(self, request):
        search = request.GET.get('search', '')
        status_filter = request.GET.get('status', '')

        debtors = CanteenDebtor.objects.all()
        if search:
            debtors = debtors.filter(
                Q(name__icontains=search)
                | Q(phone__icontains=search)
                | Q(description__icontains=search)
            )
        if status_filter == 'paid':
            debtors = debtors.filter(paid=True)
        elif status_filter == 'unpaid':
            debtors = debtors.filter(paid=False)

        debtors = debtors.order_by('-purchase_date')
        paginator = Paginator(debtors, 10)
        debtors_page = paginator.get_page(request.GET.get('page', 1))

        context = {
            'debtors': debtors_page,
            'search': search,
            'status_filter': status_filter,
            'total_debtors': CanteenDebtor.objects.count(),
            'total_unpaid': CanteenDebtor.objects.filter(paid=False).count(),
            'total_amount_unpaid': CanteenDebtor.objects.filter(paid=False).aggregate(total=Sum('amount'))['total'] or 0,
            'total_amount_paid': CanteenDebtor.objects.filter(paid=True).aggregate(total=Sum('amount'))['total'] or 0,
            'avg_debt_amount': CanteenDebtor.objects.aggregate(avg=Avg('amount'))['avg'] or 0,
            'recent_payments': CanteenDebtor.objects.filter(
                paid=True, paid_date__gte=timezone.now() - timedelta(days=30),
            ).count(),
        }
        return render(request, 'admin_panel/cantina/list.html', context)


class CanteenEditView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Cria ou edita um registro de fiado da cantina"""
    module_name = 'canteen'

    def get(self, request, debtor_id=None):
        debtor = get_object_or_404(CanteenDebtor, id=debtor_id) if debtor_id else None
        form = CanteenDebtorForm(instance=debtor)
        return render(request, 'admin_panel/cantina/edit.html', {
            'form': form, 'debtor': debtor, 'is_edit': debtor_id is not None,
        })

    def post(self, request, debtor_id=None):
        debtor = get_object_or_404(CanteenDebtor, id=debtor_id) if debtor_id else None
        form = CanteenDebtorForm(request.POST, instance=debtor)
        if form.is_valid():
            form.save()
            action = 'atualizado' if debtor_id else 'criado'
            messages.success(request, f'Registro de fiado {action} com sucesso!')
            return redirect('admin_cantina_list')
        return render(request, 'admin_panel/cantina/edit.html', {
            'form': form, 'debtor': debtor, 'is_edit': debtor_id is not None,
        })


class CanteenDetailView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Exibe os detalhes de um registro de fiado da cantina"""

    def get(self, request, debtor_id):
        debtor = get_object_or_404(CanteenDebtor, id=debtor_id)
        return render(request, 'admin_panel/cantina/detail.html', {'debtor': debtor})


class CanteenDeleteView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Exclui um registro de fiado da cantina"""

    def get(self, request, debtor_id):
        debtor = get_object_or_404(CanteenDebtor, id=debtor_id)
        return render(request, 'admin_panel/cantina/delete.html', {'debtor': debtor})

    def post(self, request, debtor_id):
        debtor = get_object_or_404(CanteenDebtor, id=debtor_id)
        debtor.delete()
        messages.success(request, 'Registro de fiado excluído com sucesso!')
        return redirect('admin_cantina_list')


class CanteenTogglePaidView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Toggle do status de pagamento de um fiado"""

    def post(self, request, debtor_id):
        debtor = get_object_or_404(CanteenDebtor, id=debtor_id)
        debtor.paid = not debtor.paid
        debtor.paid_date = timezone.now().date() if debtor.paid else None
        debtor.save()

        status = 'pago' if debtor.paid else 'não pago'
        messages.success(request, f'Status alterado para {status} com sucesso!')
        return redirect('admin_cantina_list')


@method_decorator(csrf_exempt, name='dispatch')
class CanteenApiView(View):
    """API para operações CRUD da cantina via AJAX"""

    def post(self, request):
        try:
            data = json.loads(request.body)

            if 'id' in data:
                debtor = get_object_or_404(CanteenDebtor, id=data['id'])
                for field in ('name', 'phone', 'purchase_date', 'amount', 'description', 'paid', 'notes'):
                    if field in data:
                        setattr(debtor, field, data[field])
                if 'paid' in data:
                    debtor.paid = data['paid']
                    debtor.paid_date = timezone.now().date() if debtor.paid else None
                debtor.save()
                message = 'Registro atualizado com sucesso!'
            else:
                debtor = CanteenDebtor.objects.create(
                    name=data.get('name'),
                    phone=data.get('phone'),
                    purchase_date=data.get('purchase_date', timezone.now().date()),
                    amount=data.get('amount'),
                    description=data.get('description', ''),
                    paid=data.get('paid', False),
                    notes=data.get('notes', ''),
                )
                if debtor.paid:
                    debtor.paid_date = timezone.now().date()
                    debtor.save()
                message = 'Novo registro criado com sucesso!'

            return JsonResponse({'success': True, 'message': message, 'id': debtor.id})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})


# ---------------------------------------------------------------------------
# Template Views (Consolidation Templates)
# ---------------------------------------------------------------------------

class TemplatesListView(LoginRequiredMixin, ConsolidationPermissionMixin, View):
    """Lista os templates de consolidação"""

    def get(self, request):
        search = request.GET.get('search', '')
        category_filter = request.GET.get('category', '')
        status_filter = request.GET.get('status', '')

        templates = FollowUpTemplate.objects.all()
        if search:
            templates = templates.filter(
                Q(name__icontains=search) | Q(description__icontains=search)
            )

        templates = templates.annotate(
            steps_count=Count('steps'), usage_count=Count('followup'),
        ).order_by('-created_at')

        total_templates = FollowUpTemplate.objects.count()
        active_templates = templates.filter(followup__isnull=False).distinct().count()
        total_usage = FollowUp.objects.filter(template__isnull=False).count()

        return render(request, 'admin_panel/templates.html', {
            'templates': templates,
            'search': search,
            'category_filter': category_filter,
            'status_filter': status_filter,
            'stats': {
                'total_templates': total_templates,
                'active_templates': active_templates,
                'total_usage': total_usage,
            },
        })


class TemplateCreateView(LoginRequiredMixin, ConsolidationPermissionMixin, View):
    """Criar novo template de consolidação"""

    def get(self, request):
        duplicate_from_id = request.GET.get('duplicate_from')
        original_template = None
        if duplicate_from_id:
            original_template = FollowUpTemplate.objects.filter(id=duplicate_from_id).first()

        if original_template:
            form = FollowUpTemplateForm(initial={
                'name': f'Cópia de {original_template.name}',
                'description': original_template.description,
            })
        else:
            form = FollowUpTemplateForm()

        formset = FollowUpTemplateStepFormSetForCreate()
        return render(request, 'admin_panel/template_form.html', {
            'form': form, 'formset': formset,
            'title': f'Duplicar Template: {original_template.name}' if original_template else 'Novo Template de Consolidação',
            'original_template': original_template,
        })

    def post(self, request):
        duplicate_from_id = request.POST.get('duplicate_from')
        original_template = None
        if duplicate_from_id:
            original_template = FollowUpTemplate.objects.filter(id=duplicate_from_id).first()

        if original_template:
            form = FollowUpTemplateForm(request.POST)
            if form.is_valid():
                template = form.save(commit=False)
                template.description = original_template.description
                template.save()

                for step in original_template.steps.all():
                    FollowUpTemplateStep.objects.create(
                        template=template, period=step.period,
                        period_type=step.period_type, title=step.title,
                        description=step.description,
                    )

                messages.success(request, f'Template "{template.name}" criado com sucesso a partir de "{original_template.name}"!')
                return redirect('admin_template_detail', template_id=template.id)
            else:
                messages.error(request, 'Erro ao duplicar template. Verifique os dados informados.')
        else:
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

        formset = FollowUpTemplateStepFormSetForCreate(request.POST) if not original_template else FollowUpTemplateStepFormSetForCreate()
        return render(request, 'admin_panel/template_form.html', {
            'form': form, 'formset': formset,
            'title': f'Duplicar Template: {original_template.name}' if original_template else 'Novo Template de Consolidação',
            'original_template': original_template,
        })


class TemplateEditView(LoginRequiredMixin, ConsolidationPermissionMixin, View):
    """Editar template de consolidação"""

    def get(self, request, template_id):
        template = get_object_or_404(FollowUpTemplate, id=template_id)
        form = FollowUpTemplateForm(instance=template)
        formset = FollowUpTemplateStepFormSet(instance=template)
        return render(request, 'admin_panel/template_form.html', {
            'form': form, 'formset': formset,
            'template': template, 'title': f'Editar Template: {template.name}',
        })

    def post(self, request, template_id):
        template = get_object_or_404(FollowUpTemplate, id=template_id)
        form = FollowUpTemplateForm(request.POST, instance=template)
        formset = FollowUpTemplateStepFormSet(request.POST, instance=template)

        if form.is_valid() and formset.is_valid():
            template = form.save()
            formset.save()
            messages.success(request, f'Template "{template.name}" atualizado com sucesso!')
            return redirect('admin_template_detail', template_id=template.id)

        messages.error(request, 'Erro ao atualizar template. Verifique os dados informados.')
        return render(request, 'admin_panel/template_form.html', {
            'form': form, 'formset': formset,
            'template': template, 'title': f'Editar Template: {template.name}',
        })


class TemplateDetailView(LoginRequiredMixin, ConsolidationPermissionMixin, View):
    """Visualizar detalhes do template"""

    def get(self, request, template_id):
        template = get_object_or_404(FollowUpTemplate, id=template_id)
        steps = template.steps.all().order_by('week')
        followups_using = FollowUp.objects.filter(template=template)
        return render(request, 'admin_panel/template_detail.html', {
            'template': template, 'steps': steps,
            'followups_using': followups_using,
            'usage_count': followups_using.count(),
        })


class TemplateDeleteView(LoginRequiredMixin, ConsolidationPermissionMixin, View):
    """Excluir template de consolidação"""

    def get(self, request, template_id):
        template = get_object_or_404(FollowUpTemplate, id=template_id)
        followups_using = FollowUp.objects.filter(template=template)
        return render(request, 'admin_panel/template_delete.html', {
            'template': template, 'followups_using': followups_using,
        })

    def post(self, request, template_id):
        template = get_object_or_404(FollowUpTemplate, id=template_id)
        template_name = template.name
        template.delete()
        messages.success(request, f'Template "{template_name}" excluído com sucesso!')
        return redirect('admin_templates')


# ---------------------------------------------------------------------------
# Reports View
# ---------------------------------------------------------------------------

class ReportsView(LoginRequiredMixin, ConsolidationPermissionMixin, View):
    """Relatórios de consolidação com dados reais"""

    def get(self, request):
        total_followups = FollowUp.objects.count()
        completed_followups = 0
        overdue_followups = 0

        if total_followups > 0:
            for followup in FollowUp.objects.all():
                if followup.is_completed:
                    completed_followups += 1
                elif followup.is_overdue:
                    overdue_followups += 1

        active_followups = total_followups - completed_followups
        completion_rate = (completed_followups / total_followups * 100) if total_followups else 0

        recent_followups = FollowUp.objects.select_related(
            'accompanied', 'responsible', 'template',
        ).order_by('-created_at')[:10]

        template_stats = []
        for tmpl in FollowUpTemplate.objects.all():
            tmpl_followups = FollowUp.objects.filter(template=tmpl)
            if tmpl_followups.exists():
                total_progress = sum(f.progress_percentage for f in tmpl_followups)
                count = tmpl_followups.count()
                template_stats.append({
                    'name': tmpl.name,
                    'progress': total_progress / count if count else 0,
                    'total_followups': count,
                })

        return render(request, 'admin_panel/reports.html', {
            'title': 'Relatórios de Consolidação',
            'total_followups': total_followups,
            'active_followups': active_followups,
            'completed_followups': completed_followups,
            'overdue_followups': overdue_followups,
            'completion_rate': completion_rate,
            'recent_followups': recent_followups,
            'template_stats': template_stats,
        })
