import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from ..mixins import AdminRequiredMixin
from ...models.ministry import Ministry


class MinistriesListView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Lista de ministérios"""

    def _get_queryset(self):
        return Ministry.objects.annotate(member_count=Count('memberships')).order_by('name')

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
            color = request.POST.get('color', '#F97316').strip() or '#F97316'
            is_active = request.POST.get('is_active') == 'on'

            if not name:
                messages.error(request, 'Nome do ministério é obrigatório.')
                return render(request, 'admin_panel/ministries/edit.html', {'ministry': ministry})

            if ministry:
                ministry.name = name
                ministry.description = description
                ministry.color = color
                ministry.is_active = is_active
                ministry.save()
                messages.success(request, 'Ministério atualizado com sucesso!')
            else:
                Ministry.objects.create(
                    name=name,
                    description=description,
                    color=color,
                    is_active=is_active,
                )
                messages.success(request, 'Ministério criado com sucesso!')

            return redirect('ministry_list')

        except Exception as e:
            messages.error(request, f'Erro ao salvar ministério: {e}')

        return render(request, 'admin_panel/ministries/edit.html', {'ministry': ministry})
