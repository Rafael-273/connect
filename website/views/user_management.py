from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views import View

User = get_user_model()


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Mixin that requires user_type == 'admin'."""

    def test_func(self):
        return getattr(self.request.user, 'user_type', None) == 'admin'

    def handle_no_permission(self):
        messages.error(self.request, 'Você não tem permissão para acessar esta página.')
        return redirect('admin_dashboard')


class UserManagementView(AdminRequiredMixin, View):
    """View para gerenciar usuários - visualizar, resetar senhas"""

    def get(self, request):
        users = User.objects.all().select_related('member')
        context = {'users': users}
        return render(request, 'admin_panel/users/list.html', context)


class ResetUserPasswordView(AdminRequiredMixin, View):
    """Resetar a senha do usuário para a senha padrão '123'"""

    def post(self, request, user_id):
        try:
            user = User.objects.get(id=user_id)
            user.set_password("123")
            user.save()
            messages.success(request, f"A senha de {user.email} foi redefinida para a senha padrão '123'")
        except User.DoesNotExist:
            messages.error(request, "Usuário não encontrado")
        return redirect('admin_user_management')

    def get(self, request, user_id):
        return self.post(request, user_id)


class ChangePasswordView(LoginRequiredMixin, View):
    """Permitir que o usuário altere sua própria senha"""

    def get(self, request):
        return render(request, 'admin_panel/users/change_password.html')

    def post(self, request):
        current_password = request.POST.get('current_password')
        new_password = request.POST.get('new_password')
        confirm_password = request.POST.get('confirm_password')

        if not request.user.check_password(current_password):
            messages.error(request, 'Senha atual incorreta')
            return render(request, 'admin_panel/users/change_password.html')

        if new_password != confirm_password:
            messages.error(request, 'As novas senhas não coincidem')
            return render(request, 'admin_panel/users/change_password.html')

        request.user.set_password(new_password)
        request.user.save()
        update_session_auth_hash(request, request.user)

        messages.success(request, 'Senha alterada com sucesso')
        return redirect('admin_members_list')
