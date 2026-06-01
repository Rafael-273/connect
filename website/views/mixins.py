from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import redirect

from ..models.ministry_membership import MinistryMembership


class StaffRequiredMixin(UserPassesTestMixin):
    """Mixin that requires user to be staff or admin."""

    def test_func(self):
        return self.request.user.is_staff or getattr(self.request.user, 'user_type', None) == 'admin'

    def handle_no_permission(self):
        messages.error(self.request, 'Você não tem permissão para acessar esta página.')
        return redirect('admin_dashboard')


class MemberRequiredMixin(LoginRequiredMixin):
    """Mixin that requires user to have an associated Member profile."""
    login_url = 'member_login'
    member = None

    def dispatch(self, request, *args, **kwargs):
        # LoginRequiredMixin handles unauthenticated users first
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)

        if not hasattr(request.user, 'member'):
            if request.user.is_superuser:
                return redirect('admin_dashboard')
            from django.contrib.auth import logout
            messages.error(request, 'Você precisa estar cadastrado como membro para acessar esta área.')
            logout(request)
            return redirect('member_login')

        self.member = request.user.member
        return super().dispatch(request, *args, **kwargs)


class ApproverRequiredMixin(MemberRequiredMixin):
    """Mixin that requires user to be a word-of-knowledge approver or staff."""

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        if hasattr(self, 'member') and self.member:
            if not self.member.is_approver and not request.user.is_staff:
                messages.error(request, 'Você não tem permissão para aprovar palavras.')
                return redirect('admin_dashboard')
        return response


class AdminRequiredMixin(UserPassesTestMixin):
    """Mixin that requires user to have admin panel access (has_admin_access)."""

    def test_func(self):
        return self.request.user.has_admin_access()

    def handle_no_permission(self):
        messages.error(self.request, 'Você não tem permissão para acessar esta página.')
        return redirect('admin_dashboard')


class ModulePermissionMixin(UserPassesTestMixin):
    """Mixin that checks module-specific permissions via has_module_permission."""
    module_name = None

    def test_func(self):
        user = self.request.user
        return user.is_superuser or user.has_module_permission(self.module_name)

    def handle_no_permission(self):
        messages.error(self.request, 'Você não tem permissão para acessar este módulo.')
        return redirect('admin_dashboard')


class ConsolidationPermissionMixin(UserPassesTestMixin):
    """Mixin for consolidation admin/template/report views."""

    def test_func(self):
        user = self.request.user
        return user.is_superuser or getattr(user, 'user_type', None) in ('admin', 'consolidation')

    def handle_no_permission(self):
        messages.error(self.request, 'Você não tem permissão para acessar esta página.')
        return redirect('admin_dashboard')


class MinistrationContextMixin:
    """Mixin that adds ministration membership flag to context."""

    def get_ministration_status(self, member):
        return MinistryMembership.objects.filter(
            member=member,
            ministry__name__icontains='ministração',
            is_active=True,
        ).exists()

    def get_media_status(self, member):
        return MinistryMembership.objects.filter(
            member=member,
            ministry__name__iexact='Mídia Externa',
            is_active=True,
        ).exists()
