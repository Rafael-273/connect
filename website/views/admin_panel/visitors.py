import json
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from ..mixins import ModulePermissionMixin
from ...forms.visitor import VisitorAdminForm
from ...models.member import Member
from ...models.visitor import Visitor
from .members import convert_visitor_to_member, should_convert_visitor_to_member

User = get_user_model()


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


class VisitorDetailApiView(LoginRequiredMixin, ModulePermissionMixin, View):
    """API para obter detalhes de um visitante"""
    module_name = 'visitors'

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
