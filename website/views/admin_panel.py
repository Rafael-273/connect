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
from ..forms.member import MemberAdminForm
from ..forms.visitor import VisitorAdminForm
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
from ..models.testimony import Testimony

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
    def _get_totals(self):
        return {
            'total_members': Member.objects.filter(is_active=True).count(),
            'total_visitors': Visitor.objects.count(),
            'total_events': Event.objects.count(),
        }

    def _get_recent_stats(self, last_month):
        recent_visitor_conversions = Visitor.objects.filter(
            visit_date__gte=last_month, decision_for_jesus=True,
        ).count()
        recent_member_conversions = Member.objects.filter(
            conversion_date__gte=last_month.date(), conversion='new_convert',
        ).count()
        return {
            'recent_conversions': recent_visitor_conversions + recent_member_conversions,
        }

    def _get_visitors_chart_data(self):
        visitors_by_month = []
        for i in range(6):
            month_start = (timezone.now() - timedelta(days=30 * i)).replace(day=1)
            month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            count = Visitor.objects.filter(
                visit_date__gte=month_start, visit_date__lte=month_end,
            ).count()
            visitors_by_month.append({'month': month_start.strftime('%b/%Y'), 'count': count})
        visitors_by_month.reverse()
        return json.dumps(visitors_by_month)

    def _get_lists(self):
        return {
            'ministries_stats': Ministry.objects.annotate(
                member_count=Count('member'),
            ).order_by('-member_count')[:5],
            'recent_visitors_list': Visitor.objects.order_by('-visit_date')[:5],
            'recently_converted_members': Member.objects.filter(
                conversion='new_convert',
            ).order_by('-update_at')[:3],
            'upcoming_events_list': Event.objects.filter(
                event_date__gte=timezone.now().date(),
            ).order_by('event_date')[:5],
        }

    def _build_context(self):
        last_month = timezone.now() - timedelta(days=30)
        return {
            **self._get_totals(),
            **self._get_recent_stats(last_month),
            'visitors_by_month_json': self._get_visitors_chart_data(),
            **self._get_lists(),
        }

    def get(self, request):
        return render(request, 'admin_panel/dashboard.html', self._build_context())


# ---------------------------------------------------------------------------
# List Views
# ---------------------------------------------------------------------------

class MembersListView(LoginRequiredMixin, AdminRequiredMixin, View):
    def _get_queryset(self):
        return Member.objects.select_related('user', 'neighborhood', 'spouse').order_by('-id')

    def _apply_filters(self, qs, search, ministry_filter, status_filter):
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(user__email__icontains=search)
                | Q(phone__icontains=search)
            )
        if ministry_filter:
            qs = qs.filter(ministry_memberships__ministry_id=ministry_filter).distinct()
        if status_filter == 'active':
            qs = qs.filter(is_active=True)
        elif status_filter == 'inactive':
            qs = qs.filter(is_active=False)
        elif status_filter == 'new_convert':
            qs = qs.filter(conversion='new_convert')
        return qs

    def _build_context(self, request):
        search = request.GET.get('search', '')
        ministry_filter = request.GET.get('ministry', '')
        status_filter = request.GET.get('status', '')

        qs = self._apply_filters(self._get_queryset(), search, ministry_filter, status_filter)
        members = Paginator(qs, 20).get_page(request.GET.get('page'))

        return {
            'members': members,
            'ministries': Ministry.objects.all(),
            'search': search,
            'ministry_filter': ministry_filter,
            'status_filter': status_filter,
        }

    def get(self, request):
        if 'member_success_message' in request.session:
            messages.success(request, request.session.pop('member_success_message'))
        return render(request, 'admin_panel/members/list.html', self._build_context(request))


class VisitorsListView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Lista de visitantes com filtros e busca"""
    module_name = 'visitors'

    def _get_queryset(self):
        return Visitor.objects.select_related('neighborhood').order_by('-visit_date', '-id')

    def _apply_filters(self, qs, search, period_filter, conversion_filter):
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(email__icontains=search)
                | Q(phone__icontains=search)
            )
        if period_filter == '7_days':
            qs = qs.filter(visit_date__gte=timezone.now() - timedelta(days=7))
        elif period_filter == '30_days':
            qs = qs.filter(visit_date__gte=timezone.now() - timedelta(days=30))
        if conversion_filter == 'converted':
            qs = qs.filter(decision_for_jesus=True)
        elif conversion_filter == 'not_converted':
            qs = qs.filter(decision_for_jesus=False)
        return qs

    def _build_context(self, request):
        search = request.GET.get('search', '')
        period_filter = request.GET.get('period', '')
        conversion_filter = request.GET.get('conversion', '')

        qs = self._apply_filters(self._get_queryset(), search, period_filter, conversion_filter)
        visitors = Paginator(qs, 20).get_page(request.GET.get('page'))

        return {
            'visitors': visitors,
            'search': search,
            'period_filter': period_filter,
            'conversion_filter': conversion_filter,
        }

    def get(self, request):
        return render(request, 'admin_panel/visitors/list.html', self._build_context(request))


class EventsListView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Lista de eventos com filtros e busca"""
    module_name = 'events'

    def _get_queryset(self):
        return Event.objects.all()

    def _apply_filters(self, qs, search, status_filter):
        if search:
            qs = qs.filter(
                Q(title__icontains=search)
                | Q(description__icontains=search)
                | Q(location__icontains=search)
            )
        if status_filter:
            today = timezone.now().date()
            if status_filter == 'upcoming':
                qs = qs.filter(event_date__gte=today)
            elif status_filter == 'past':
                qs = qs.filter(event_date__lt=today)
        return qs.order_by('-event_date')

    def _build_context(self, request):
        search = request.GET.get('search', '')
        status_filter = request.GET.get('status', '')

        qs = self._apply_filters(self._get_queryset(), search, status_filter)
        events = Paginator(qs, 20).get_page(request.GET.get('page'))

        return {
            'events': events,
            'search': search,
            'status_filter': status_filter,
        }

    def get(self, request):
        return render(request, 'admin_panel/events/list.html', self._build_context(request))


class MinistriesListView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Lista de ministérios"""

    def _get_queryset(self):
        return Ministry.objects.annotate(member_count=Count('member')).order_by('name')

    def _apply_filters(self, qs, search):
        if search:
            qs = qs.filter(name__icontains=search)
        return qs

    def _build_context(self, request):
        search = request.GET.get('search', '')

        qs = self._apply_filters(self._get_queryset(), search)
        ministries = Paginator(qs, 20).get_page(request.GET.get('page'))

        return {
            'ministries': ministries,
            'search': search,
        }

    def get(self, request):
        return render(request, 'admin_panel/ministries/list.html', self._build_context(request))


class NeighborhoodsListView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Lista de bairros"""

    def _get_queryset(self):
        return Neighborhood.objects.select_related('parent').order_by('name')

    def _apply_filters(self, qs, search):
        if search:
            qs = qs.filter(name__icontains=search)
        return qs

    def _build_context(self, request):
        search = request.GET.get('search', '')

        qs = self._apply_filters(self._get_queryset(), search)
        neighborhoods = Paginator(qs, 20).get_page(request.GET.get('page'))

        return {
            'neighborhoods': neighborhoods,
            'search': search,
        }

    def get(self, request):
        return render(request, 'admin_panel/neighborhoods/list.html', self._build_context(request))


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
            'ministry': ', '.join(member.ministry.values_list('name', flat=True)) or None,
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
            'birth_date': visitor.birth_date.strftime('%d/%m/%Y') if visitor.birth_date else None,
            'address': visitor.address,
            'gender': visitor.get_gender_display() if visitor.gender else None,
            'neighborhood': visitor.neighborhood.name if visitor.neighborhood else None,
            'visit_date': visitor.visit_date.strftime('%d/%m/%Y'),
            'decision_for_jesus': visitor.decision_for_jesus,
            'conversion': visitor.get_conversion_display() if visitor.conversion else None,
            'wants_home_prayer': getattr(visitor, 'wants_home_prayer', False),
            'is_member': '[CONVERTIDO]' in (visitor.profile_notes or ''),
            'prayer_request': visitor.prayer_request,
            'profile_notes': visitor.profile_notes,
        })


# ---------------------------------------------------------------------------
# Edit / Create Views
# ---------------------------------------------------------------------------

class MemberEditView(LoginRequiredMixin, AdminRequiredMixin, View):
    _TEMPLATE = 'admin_panel/members/edit.html'

    def get(self, request, member_id=None):
        messages.get_messages(request).used = True
        member = self._get_member(member_id)
        form = MemberAdminForm(instance=member, initial=self._user_initial(member))
        return render(request, self._TEMPLATE, {'form': form, 'member': member})

    def post(self, request, member_id=None):
        member = self._get_member(member_id)
        form = MemberAdminForm(
            request.POST, request.FILES,
            instance=member,
            initial=self._user_initial(member),
        )
        if form.is_valid():
            try:
                self._save_member(form, member)
                request.session['member_success_message'] = (
                    'Membro atualizado com sucesso!' if member else 'Membro criado com sucesso!'
                )
                return redirect('admin_members_list')
            except Exception as exc:
                self._handle_db_error(request, exc)

        return render(request, self._TEMPLATE, {'form': form, 'member': member})

    @staticmethod
    def _get_member(member_id):
        if not member_id:
            return None
        return get_object_or_404(
            Member.objects.select_related('user'), id=member_id
        )

    @staticmethod
    def _user_initial(member):
        if member and member.user:
            return {'email': member.user.email, 'user_type': member.user.user_type}
        return {'user_type': 'member'}

    @staticmethod
    def _save_member(form, member):
        email     = form.cleaned_data.get('email') or None
        user_type = form.cleaned_data.get('user_type') or 'member'

        if member is None:
            if not email:
                email = f"membro_{uuid.uuid4().hex[:8]}@autogerado.com"
            user = User.objects.create_user(
                email=email,
                password='123',
                user_type=user_type,
                is_staff=user_type != 'member',
            )
            member = form.save(commit=False)
            member.user = user
            member.save()
            form.save_m2m()
        else:
            if member.user and email:
                member.user.email     = email
                member.user.user_type = user_type
                member.user.is_staff  = user_type != 'member'
                member.user.save(update_fields=['email', 'user_type', 'is_staff'])
            form.save()

        return member

    @staticmethod
    def _handle_db_error(request, exc):
        msg = str(exc)
        if 'duplicate key value' in msg and 'email' in msg:
            messages.error(request, 'Já existe um usuário com este email. Por favor, escolha outro email.')
        elif 'duplicate key value' in msg:
            messages.error(request, f'Erro de integridade: {msg}')
        else:
            messages.error(request, f'Erro ao salvar membro: {msg}')


class VisitorEditView(LoginRequiredMixin, ModulePermissionMixin, View):
    module_name = 'visitors'
    _TEMPLATE = 'admin_panel/visitors/edit.html'

    def get(self, request, visitor_id=None):
        messages.get_messages(request).used = True
        visitor = self._get_visitor(visitor_id)
        form = VisitorAdminForm(instance=visitor)
        visit_date_value = visitor.visit_date.strftime('%Y-%m-%d') if visitor and visitor.visit_date else ''
        return render(request, self._TEMPLATE, {
            'form': form,
            'visitor': visitor,
            'visit_date_value': visit_date_value,
            'email_warning': None,
        })

    def post(self, request, visitor_id=None):
        visitor = self._get_visitor(visitor_id)
        old_decision = visitor.decision_for_jesus if visitor else False
        old_conversion = visitor.conversion if visitor else None

        form = VisitorAdminForm(request.POST, instance=visitor)
        email_warning = None
        visit_date = self._parse_visit_date(request)

        if visit_date is None:
            messages.error(request, 'Data da visita é obrigatória e deve estar no formato AAAA-MM-DD.')
        elif form.is_valid():
            try:
                email_warning = self._get_email_warning(form.cleaned_data.get('email'))
                self._check_phone_duplicate(request, form.cleaned_data.get('phone'), visitor)
                visitor = form.save(visit_date=visit_date)
                self._handle_conversion(request, visitor, old_decision, old_conversion)
                return redirect('admin_visitors_list')
            except Exception as exc:
                self._handle_db_error(request, exc)

        return render(request, self._TEMPLATE, {
            'form': form,
            'visitor': visitor,
            'visit_date_value': request.POST.get('visit_date', '') or (visitor.visit_date.strftime('%Y-%m-%d') if visitor and visitor.visit_date else ''),
            'email_warning': json.dumps(email_warning) if email_warning else None,
        })

    @staticmethod
    def _get_visitor(visitor_id):
        if not visitor_id:
            return None
        return get_object_or_404(Visitor, id=visitor_id)

    @staticmethod
    def _parse_visit_date(request):
        from datetime import datetime
        raw = request.POST.get('visit_date', '').strip()
        if not raw:
            return None
        try:
            return datetime.strptime(raw, '%Y-%m-%d').date()
        except ValueError:
            return None

    @staticmethod
    def _get_email_warning(email):
        if not email:
            return None
        existing_user = User.objects.filter(email=email).first()
        if not existing_user:
            return None
        try:
            existing_member = Member.objects.filter(user=existing_user).first()
            if existing_member:
                return {
                    'type': 'member_conflict',
                    'email': email,
                    'member_name': existing_member.name,
                    'message': f'O email "{email}" já pertence ao membro "{existing_member.name}". Verifique se são a mesma pessoa.',
                }
            return {
                'type': 'user_conflict',
                'email': email,
                'message': f'O email "{email}" já está registrado no sistema como usuário. Verifique possíveis duplicatas.',
            }
        except Exception:
            return {
                'type': 'general_conflict',
                'email': email,
                'message': f'O email "{email}" já está registrado no sistema. Verifique possíveis duplicatas.',
            }

    @staticmethod
    def _check_phone_duplicate(request, phone, visitor):
        if not phone:
            return
        phone_clean = phone.replace('(', '').replace(')', '').replace('-', '').replace(' ', '')
        qs = Visitor.objects.filter(phone__isnull=False)
        if visitor:
            qs = qs.exclude(id=visitor.id)
        for existing in qs:
            if existing.phone:
                existing_clean = existing.phone.replace('(', '').replace(')', '').replace('-', '').replace(' ', '')
                if existing_clean == phone_clean:
                    messages.warning(
                        request,
                        f'O telefone "{phone}" já está cadastrado para o visitante "{existing.name}". Verifique se são a mesma pessoa.',
                    )
                    break

    @staticmethod
    def _handle_conversion(request, visitor, old_decision, old_conversion):
        """Converte visitante para membro quando aplicável e adiciona mensagem de sucesso."""
        should_convert = should_convert_visitor_to_member(visitor)
        was_convertible = old_decision or old_conversion

        if should_convert:
            already_converted = '[CONVERTIDO]' in (visitor.profile_notes or '')
            if not was_convertible or not already_converted:
                member = convert_visitor_to_member(visitor)
                if member:
                    messages.success(request, f'✅ Visitante convertido para membro com sucesso! (Tipo: {member.conversion})')
                else:
                    messages.warning(request, 'Visitante salvo, mas houve erro na conversão para membro.')
            else:
                messages.success(request, 'Visitante atualizado com sucesso! (Já era convertido anteriormente)')
        else:
            messages.success(request, 'Visitante salvo com sucesso!')

    @staticmethod
    def _handle_db_error(request, exc):
        msg = str(exc)
        if 'duplicate key value' in msg and 'email' in msg:
            messages.error(request, 'Já existe um visitante com este email. Por favor, escolha outro email.')
        elif 'duplicate key value' in msg:
            messages.error(request, f'Erro de integridade: {msg}')
        else:
            messages.error(request, f'Erro ao salvar visitante: {msg}')


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
    module_name = 'consolidation'
    _TEMPLATE = 'admin_panel/followups/edit.html'

    def get(self, request, followup_id=None):
        followup = self._get_followup(followup_id)
        form = FollowUpForm(instance=followup)
        return render(request, self._TEMPLATE, {
            'form': form,
            'followup': followup,
            'templates_data': self._get_templates_data(),
        })

    def post(self, request, followup_id=None):
        followup = self._get_followup(followup_id)
        form = FollowUpForm(request.POST, instance=followup)
        if form.is_valid():
            form.save()
            messages.success(request, 'Follow-up salvo com sucesso!')
            return redirect('admin_followups_list')

        messages.error(request, 'Erro ao salvar follow-up. Verifique os dados.')
        return render(request, self._TEMPLATE, {
            'form': form,
            'followup': followup,
            'templates_data': self._get_templates_data(),
        })

    @staticmethod
    def _get_followup(followup_id):
        if not followup_id:
            return None
        return get_object_or_404(FollowUp, id=followup_id)

    @staticmethod
    def _get_templates_data():
        rows = FollowUpTemplate.objects.annotate(
            steps_count_val=Count('steps'),
        ).values_list('id', 'name', 'description', 'steps_count_val')
        return [
            {'id': t[0], 'name': t[1], 'description': t[2] or 'Sem descrição', 'steps_count': t[3]}
            for t in rows
        ]


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


# FBV alias — mantido para compatibilidade com urls.py
templates_view = TemplatesListView.as_view()


class TemplateCreateView(LoginRequiredMixin, ConsolidationPermissionMixin, View):
    _TEMPLATE = 'admin_panel/template_form.html'

    def get(self, request):
        original_template = self._get_original_template(request.GET.get('duplicate_from'))
        if original_template:
            form = FollowUpTemplateForm(initial={
                'name': f'Cópia de {original_template.name}',
                'description': original_template.description,
            })
        else:
            form = FollowUpTemplateForm()
        formset = FollowUpTemplateStepFormSetForCreate()
        return render(request, self._TEMPLATE, self._build_context(form, formset, original_template))

    def post(self, request):
        original_template = self._get_original_template(request.POST.get('duplicate_from'))
        form = FollowUpTemplateForm(request.POST)

        if original_template:
            if form.is_valid():
                template = self._duplicate_template(form, original_template)
                messages.success(request, f'Template "{template.name}" criado com sucesso a partir de "{original_template.name}"!')
                return redirect('admin_template_detail', template_id=template.id)
            messages.error(request, 'Erro ao duplicar template. Verifique os dados informados.')
            formset = FollowUpTemplateStepFormSetForCreate()
        else:
            formset = FollowUpTemplateStepFormSetForCreate(request.POST)
            if form.is_valid() and formset.is_valid():
                template = self._create_from_formset(form, formset)
                messages.success(request, f'Template "{template.name}" criado com sucesso!')
                return redirect('admin_template_detail', template_id=template.id)
            messages.error(request, 'Erro ao criar template. Verifique os dados informados.')

        return render(request, self._TEMPLATE, self._build_context(form, formset, original_template))

    @staticmethod
    def _get_original_template(template_id):
        if not template_id:
            return None
        return FollowUpTemplate.objects.filter(id=template_id).first()

    @staticmethod
    def _build_context(form, formset, original_template):
        return {
            'form': form,
            'formset': formset,
            'title': f'Duplicar Template: {original_template.name}' if original_template else 'Novo Template de Consolidação',
            'original_template': original_template,
        }

    @staticmethod
    def _duplicate_template(form, original_template):
        template = form.save(commit=False)
        template.description = original_template.description
        template.save()
        for step in original_template.steps.all():
            FollowUpTemplateStep.objects.create(
                template=template, period=step.period,
                period_type=step.period_type, title=step.title,
                description=step.description,
            )
        return template

    @staticmethod
    def _create_from_formset(form, formset):
        template = form.save()
        formset.instance = template
        formset.save()
        return template


# FBV alias — mantido para compatibilidade com urls.py
template_create_view = TemplateCreateView.as_view()


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


# FBV alias — mantido para compatibilidade com urls.py
template_detail_view = TemplateDetailView.as_view()


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


# FBV alias — mantido para compatibilidade com urls.py
template_edit_view = TemplateEditView.as_view()
template_delete_view = TemplateDeleteView.as_view()


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


# FBV alias — mantido para compatibilidade com urls.py
reports_view = ReportsView.as_view()


# === TESTIMONY VIEWS ===


class TestimonyListView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Lista de testemunhos com filtros"""

    def get(self, request):
        testimonies = Testimony.objects.select_related('member').order_by('-created_at')

        search = request.GET.get('search', '').strip()
        category = request.GET.get('category', '')
        approved = request.GET.get('approved', '')

        if search:
            testimonies = testimonies.filter(Q(title__icontains=search))
        if category:
            testimonies = testimonies.filter(category=category)
        if approved == 'true':
            testimonies = testimonies.filter(is_approved=True)
        elif approved == 'false':
            testimonies = testimonies.filter(is_approved=False)

        paginator = Paginator(testimonies, 12)
        page_obj = paginator.get_page(request.GET.get('page'))

        return render(request, 'admin_panel/testimonies/list.html', {
            'testimonies': page_obj,
            'page_obj': page_obj,
            'category_choices': Testimony.CATEGORY_CHOICES,
            'total': testimonies.count(),
            'total_approved': Testimony.objects.filter(is_approved=True).count(),
            'total_pending': Testimony.objects.filter(is_approved=False).count(),
        })


class TestimonyEditView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Criar ou editar testemunho"""

    def _get_context(self, testimony=None):
        return {
            'testimony': testimony,
            'category_choices': Testimony.CATEGORY_CHOICES,
        }

    def get(self, request, testimony_id=None):
        testimony = get_object_or_404(Testimony, id=testimony_id) if testimony_id else None
        storage = messages.get_messages(request)
        storage.used = True
        return render(request, 'admin_panel/testimonies/edit.html', self._get_context(testimony))

    def post(self, request, testimony_id=None):
        testimony = get_object_or_404(Testimony, id=testimony_id) if testimony_id else None
        try:
            title = request.POST.get('title', '').strip()
            category = request.POST.get('category', 'other')
            is_approved = request.POST.get('is_approved') == 'on'
            show_on_home = request.POST.get('show_on_home') == 'on'
            instagram_url = request.POST.get('instagram_url', '').strip() or None

            if not title:
                messages.error(request, 'O título do testemunho é obrigatório.')
                return render(request, 'admin_panel/testimonies/edit.html', self._get_context(testimony))

            if testimony:
                testimony.title = title
                testimony.category = category
                testimony.is_approved = is_approved
                testimony.show_on_home = show_on_home
                testimony.instagram_url = instagram_url
                testimony.save()
                messages.success(request, 'Testemunho atualizado com sucesso!')
            else:
                Testimony.objects.create(
                    author_name='',
                    title=title,
                    category=category,
                    is_approved=is_approved,
                    show_on_home=show_on_home,
                    instagram_url=instagram_url,
                )
                messages.success(request, 'Testemunho cadastrado com sucesso!')

            return redirect('admin_testimonies_list')

        except Exception as e:
            messages.error(request, f'Erro ao salvar testemunho: {str(e)}')

        return render(request, 'admin_panel/testimonies/edit.html', self._get_context(testimony))


class TestimonyDeleteView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Deletar testemunho"""

    def get(self, request, testimony_id):
        testimony = get_object_or_404(Testimony, id=testimony_id)
        return render(request, 'admin_panel/testimonies/delete.html', {'testimony': testimony})

    def post(self, request, testimony_id):
        testimony = get_object_or_404(Testimony, id=testimony_id)
        name = testimony.title
        testimony.delete()
        messages.success(request, f'Testemunho "{name}" excluído com sucesso!')
        return redirect('admin_testimonies_list')


@method_decorator(csrf_exempt, name='dispatch')
class TestimonyToggleView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Alterna aprovação ou exibição na home via AJAX"""

    def post(self, request, testimony_id):
        testimony = get_object_or_404(Testimony, id=testimony_id)
        data = json.loads(request.body)
        field = data.get('field')  # 'is_approved' or 'show_on_home'
        if field == 'is_approved':
            testimony.is_approved = not testimony.is_approved
            testimony.save(update_fields=['is_approved'])
            return JsonResponse({'success': True, 'value': testimony.is_approved})
        elif field == 'show_on_home':
            testimony.show_on_home = not testimony.show_on_home
            testimony.save(update_fields=['show_on_home'])
            return JsonResponse({'success': True, 'value': testimony.show_on_home})
        return JsonResponse({'success': False, 'error': 'Requisição inválida'})
