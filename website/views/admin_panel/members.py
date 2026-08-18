import uuid
from datetime import datetime

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from ..mixins import AdminRequiredMixin
from ...forms.member import MemberAdminForm
from ...models.member import Member
from ...models.ministry import Ministry
from ...models.ministry_membership import MinistryMembership

User = get_user_model()


# ---------------------------------------------------------------------------
# Utility helpers (used also by visitors.py)
# ---------------------------------------------------------------------------

def should_convert_visitor_to_member(visitor):
    """Verifica se um visitante deve ser convertido para membro"""
    return visitor.decision_for_jesus or visitor.conversion


def convert_visitor_to_member(visitor):
    """Converte um visitante em membro quando ele faz decisão por Jesus ou tem conversão definida"""
    try:
        if visitor.email:
            existing_member = Member.objects.filter(user__email=visitor.email).first()
            if existing_member:
                return existing_member

        if visitor.profile_notes and '[CONVERTIDO]' in visitor.profile_notes:
            if visitor.email:
                return Member.objects.filter(user__email=visitor.email).first()
            return None

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
# List / Detail API / Edit Views
# ---------------------------------------------------------------------------

class MembersListView(LoginRequiredMixin, AdminRequiredMixin, View):
    def _get_queryset(self):
        return Member.objects.select_related('user', 'neighborhood', 'spouse').prefetch_related('ministry_memberships__ministry').order_by('-id')

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
            'church_role': member.get_church_role_display(),
            'ministry': ', '.join(
                MinistryMembership.objects.filter(
                    member=member, is_active=True
                ).values_list('ministry__name', flat=True)
            ) or None,
            'neighborhood': member.neighborhood.name if member.neighborhood else None,
            'marital_status': member.get_marital_status_display() if member.marital_status else None,
            'conversion': member.get_conversion_display() if member.conversion else None,
            'conversion_date': member.conversion_date.strftime('%d/%m/%Y') if member.conversion_date else None,
            'is_active': member.is_active,
            'testimony': member.testimony,
            'interests': member.interests,
            'profile_picture': member.profile_picture.url if member.profile_picture else None,
        })


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
