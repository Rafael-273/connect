from django.shortcuts import render, redirect
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils.crypto import get_random_string

User = get_user_model()

@login_required
@user_passes_test(lambda u: u.user_type == 'admin')
def user_management_view(request):
    """
    View para gerenciar usuários - visualizar, resetar senhas
    """
    users = User.objects.all().select_related('member')
    
    context = {
        'users': users
    }
    
    return render(request, 'admin_panel/users/list.html', context)

@login_required
@user_passes_test(lambda u: u.user_type == 'admin')
def reset_user_password(request, user_id):
    """
    Resetar a senha do usuário para a senha padrão "123"
    """
    try:
        user = User.objects.get(id=user_id)
        user.set_password("123")
        user.save()
        
        messages.success(request, f"A senha de {user.email} foi redefinida para a senha padrão '123'")
    except User.DoesNotExist:
        messages.error(request, "Usuário não encontrado")
    
    return redirect('admin_user_management')

@login_required
def change_password_view(request):
    """
    Permitir que o usuário altere sua própria senha
    """
    if request.method == 'POST':
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
        
        # Atualizar a sessão para evitar logout
        from django.contrib.auth import update_session_auth_hash
        update_session_auth_hash(request, request.user)
        
        messages.success(request, 'Senha alterada com sucesso')
        return redirect('admin_profile')
    
    return render(request, 'admin_panel/users/change_password.html')
