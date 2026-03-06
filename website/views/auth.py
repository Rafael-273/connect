from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.views import View

from ..models.user import User


class AdminLoginView(View):
    """Simple login view for admin panel"""

    def get(self, request):
        if request.user.is_authenticated and request.user.is_staff:
            return redirect('admin_dashboard')
        return render(request, 'admin_panel/login.html')

    def post(self, request):
        if request.user.is_authenticated and request.user.is_staff:
            return redirect('admin_dashboard')

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


class AdminLogoutView(View):
    """Logout view for admin panel"""

    def get(self, request):
        logout(request)
        return redirect('admin_login')

    def post(self, request):
        return self.get(request)
