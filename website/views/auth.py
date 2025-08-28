from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.contrib.auth.decorators import login_required


def admin_login_view(request):
    """Simple login view for admin panel"""
    if request.user.is_authenticated and request.user.is_staff:
        return redirect('admin_dashboard')
    
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        
        if email and password:
            user = authenticate(request, username=email, password=password)
            if user and user.is_staff:
                login(request, user)
                next_url = request.GET.get('next', 'admin_dashboard')
                return redirect(next_url)
            else:
                messages.error(request, 'Credenciais inválidas ou usuário sem permissão de administrador.')
        else:
            messages.error(request, 'Por favor, preencha todos os campos.')
    
    return render(request, 'admin_panel/login.html')


def admin_logout_view(request):
    """Logout view for admin panel"""
    logout(request)
    return redirect('admin_login')
