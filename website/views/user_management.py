from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models.deletion import ProtectedError
from django.views import View

from ..forms.user import UserAdminForm
from ..models.member import Member

User = get_user_model()


def _active_members_by_user_id(user_ids):
    return {
        member.user_id: member
        for member in Member.objects.filter(deleted__isnull=True, user_id__in=user_ids)
    }


def _attach_active_members(users):
    members_by_user_id = _active_members_by_user_id([user.id for user in users])
    for user in users:
        user.active_member = members_by_user_id.get(user.id)
    return users


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
        users = _attach_active_members(list(User.objects.all().order_by('-date_joined')))
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


class DeleteUserView(AdminRequiredMixin, View):
    """Excluir usuário do sistema."""

    def post(self, request, user_id):
        user = get_object_or_404(User, id=user_id)

        if user.id == request.user.id:
            messages.error(request, 'Você não pode excluir sua própria conta.')
            return redirect('admin_user_management')

        if user.is_superuser:
            messages.error(request, 'Não é possível excluir um superusuário.')
            return redirect('admin_user_management')

        active_member = Member.objects.filter(deleted__isnull=True, user=user).first()
        display_name = active_member.name if active_member else user.email

        try:
            user.delete()
        except ProtectedError:
            messages.error(
                request,
                f'Não foi possível excluir {display_name}. '
                'Este usuário possui registros vinculados no sistema (ex.: projetos de mídia).',
            )
            return redirect('admin_user_management')

        messages.success(request, f'Usuário {display_name} excluído com sucesso.')
        return redirect('admin_user_management')


class UserEditView(AdminRequiredMixin, View):
    """Editar conta de usuário (sem perfil de membro vinculado)."""

    _TEMPLATE = 'admin_panel/users/edit.html'

    def get(self, request, user_id):
        user = get_object_or_404(User, id=user_id)
        active_member = Member.objects.filter(deleted__isnull=True, user=user).first()
        if active_member:
            return redirect('admin_member_edit', member_id=active_member.id)

        form = UserAdminForm(instance=user)
        return render(request, self._TEMPLATE, {'form': form, 'user_obj': user})

    def post(self, request, user_id):
        user = get_object_or_404(User, id=user_id)
        active_member = Member.objects.filter(deleted__isnull=True, user=user).first()
        if active_member:
            return redirect('admin_member_edit', member_id=active_member.id)

        form = UserAdminForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, f'Usuário {user.email} atualizado com sucesso.')
            return redirect('admin_user_management')

        return render(request, self._TEMPLATE, {'form': form, 'user_obj': user})


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
