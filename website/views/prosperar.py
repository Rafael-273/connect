from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views import View

from ..forms.prosperar import (
    ProsperarCompanyProfileForm,
    ProsperarCompanyRegistrationForm,
    ProsperarPasswordChangeForm,
)
from ..models.prosperar import ProsperarCompany
from .mixins import ProsperarRequiredMixin


def get_user_landing_route(user):
    if user.has_admin_access():
        return 'admin_dashboard'
    if hasattr(user, 'member'):
        return 'member_dashboard'
    if hasattr(user, 'prosperar_company'):
        return 'prosperar_dashboard'
    return None


class ProsperarCompanyRegisterView(View):
    """Public registration form for Prosperar companies."""

    def get(self, request):
        return render(request, 'prosperar/public_form.html', {
            'form': ProsperarCompanyRegistrationForm(),
            'back_url': self._get_back_url(request),
        })

    def post(self, request):
        form = ProsperarCompanyRegistrationForm(request.POST, request.FILES)
        back_url = self._get_back_url(request)

        if form.is_valid():
            company = form.save()
            request.session['prosperar_register_success'] = {
                'company_name': company.company_name,
                'email': company.user.email,
                'used_existing_member_account': form.used_existing_member_account,
                'back_url': back_url,
            }
            return redirect('prosperar_register_success')

        return render(request, 'prosperar/public_form.html', {
            'form': form,
            'back_url': back_url,
        })

    def _get_back_url(self, request):
        if request.user.is_authenticated:
            landing_route = get_user_landing_route(request.user)
            if landing_route:
                return landing_route
        return 'home'


class ProsperarCompanyRegisterSuccessView(View):
    def get(self, request):
        success_data = request.session.get('prosperar_register_success')
        if not success_data:
            return redirect('prosperar_company_register')

        return render(request, 'prosperar/public_success.html', {
            'company_name': success_data.get('company_name'),
            'email': success_data.get('email'),
            'used_existing_member_account': success_data.get('used_existing_member_account', False),
            'back_url': success_data.get('back_url', 'prosperar_login'),
        })


class ProsperarLoginView(View):
    def get(self, request):
        if request.user.is_authenticated:
            if hasattr(request.user, 'prosperar_company'):
                return redirect('prosperar_dashboard')
            logout(request)
        return render(request, 'prosperar/login.html')

    def post(self, request):
        if request.user.is_authenticated:
            if hasattr(request.user, 'prosperar_company'):
                return redirect('prosperar_dashboard')
            logout(request)

        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')

        if not email or not password:
            messages.error(request, 'Por favor, preencha email e senha.')
            return render(request, 'prosperar/login.html', {'email': email})

        user = authenticate(request, username=email, password=password)
        if user is None:
            messages.error(request, 'Email ou senha incorretos.')
            return render(request, 'prosperar/login.html', {'email': email})

        if not hasattr(user, 'prosperar_company'):
            messages.error(request, 'Esta conta ainda nao possui acesso ao Prosperar.')
            return render(request, 'prosperar/login.html', {'email': email})

        login(request, user)
        messages.success(request, f'Bem-vindo(a), {user.prosperar_company.company_name}!')
        return redirect('prosperar_dashboard')


class ProsperarLogoutView(View):
    def get(self, request):
        if request.user.is_authenticated:
            company = getattr(request.user, 'prosperar_company', None)
            user_name = company.company_name if company else request.user.email
            logout(request)
            messages.success(request, f'Até logo, {user_name}! Volte sempre.')
        return redirect('prosperar_login')

    def post(self, request):
        return self.get(request)


class ProsperarDashboardView(ProsperarRequiredMixin, View):
    login_url = 'prosperar_login'

    def get(self, request):
        company = self.company
        query = request.GET.get('q', '').strip()
        sector = request.GET.get('sector', '').strip()

        companies = ProsperarCompany.objects.select_related('user', 'member', 'neighborhood').order_by('company_name')

        if query:
            companies = companies.filter(
                Q(company_name__icontains=query) |
                Q(description__icontains=query) |
                Q(neighborhood__name__icontains=query)
            )

        if sector:
            companies = companies.filter(business_sector=sector)

        return render(request, 'prosperar/dashboard.html', {
            'company': company,
            'companies': companies,
            'sector_choices': ProsperarCompany.BUSINESS_SECTOR_CHOICES,
            'query': query,
            'sector': sector,
        })


class ProsperarProfileView(ProsperarRequiredMixin, View):
    login_url = 'prosperar_login'

    def get(self, request):
        return render(request, 'prosperar/profile.html', self._build_context())

    def post(self, request):
        form_type = request.POST.get('form_type')

        if form_type == 'company_info':
            return self._handle_company_info(request)
        if form_type == 'change_password':
            return self._handle_change_password(request)

        return redirect('prosperar_profile')

    def _build_context(self, profile_form=None, password_form=None):
        return {
            'company': self.company,
            'profile_form': profile_form or ProsperarCompanyProfileForm(instance=self.company),
            'password_form': password_form or ProsperarPasswordChangeForm(user=self.request.user),
        }

    def _handle_company_info(self, request):
        form = ProsperarCompanyProfileForm(request.POST, request.FILES, instance=self.company)
        if not form.is_valid():
            messages.error(request, 'Por favor, corrija os erros abaixo.')
            return render(request, 'prosperar/profile.html', self._build_context(profile_form=form))

        form.save()
        messages.success(request, 'Perfil da empresa atualizado com sucesso!')
        return redirect('prosperar_profile')

    def _handle_change_password(self, request):
        form = ProsperarPasswordChangeForm(user=request.user, data=request.POST)
        if not form.is_valid():
            return render(request, 'prosperar/profile.html', self._build_context(password_form=form))

        form.save()
        messages.success(request, 'Senha alterada com sucesso! Faça login novamente.')
        logout(request)
        return redirect('prosperar_login')
