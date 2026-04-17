import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from ..mixins import AdminRequiredMixin
from ...models.neighborhood import Neighborhood


class NeighborhoodsListView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Lista de bairros"""

    def _get_queryset(self):
        return Neighborhood.objects.select_related('parent').order_by('name')

    def _apply_filters(self, qs, search):
        if search:
            qs = qs.filter(name__icontains=search)
        return qs

    def _build_context(self, request):
        search = request.GET.get('search', '')
        qs = self._apply_filters(self._get_queryset(), search)
        neighborhoods = Paginator(qs, 20).get_page(request.GET.get('page'))
        return {
            'neighborhoods': neighborhoods,
            'search': search,
        }

    def get(self, request):
        return render(request, 'admin_panel/neighborhoods/list.html', self._build_context(request))


@method_decorator(csrf_exempt, name='dispatch')
class NeighborhoodCreateEditApiView(LoginRequiredMixin, AdminRequiredMixin, View):
    """API para criar/editar bairros via AJAX"""

    def post(self, request):
        try:
            data = json.loads(request.body)
            neighborhood_id = data.get('id')
            name = data.get('name')
            parent_id = data.get('parent')

            parent = get_object_or_404(Neighborhood, id=parent_id) if parent_id else None

            if neighborhood_id:
                neighborhood = get_object_or_404(Neighborhood, id=neighborhood_id)
                neighborhood.name = name
                neighborhood.parent = parent
                neighborhood.save()
                return JsonResponse({'success': True, 'message': 'Bairro atualizado com sucesso!'})
            else:
                Neighborhood.objects.create(name=name, parent=parent)
                return JsonResponse({'success': True, 'message': 'Bairro criado com sucesso!'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})


class NeighborhoodEditView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Criar ou editar bairro"""

    def _get_parent_neighborhoods(self, neighborhood=None):
        qs = Neighborhood.objects.all()
        if neighborhood:
            qs = qs.exclude(id=neighborhood.id)
        return qs

    def get(self, request, neighborhood_id=None):
        neighborhood = get_object_or_404(Neighborhood, id=neighborhood_id) if neighborhood_id else None
        storage = messages.get_messages(request)
        storage.used = True
        return render(request, 'admin_panel/neighborhoods/edit.html', {
            'neighborhood': neighborhood,
            'parent_neighborhoods': self._get_parent_neighborhoods(neighborhood),
        })

    def post(self, request, neighborhood_id=None):
        neighborhood = get_object_or_404(Neighborhood, id=neighborhood_id) if neighborhood_id else None

        try:
            name = request.POST.get('name', '').strip()
            parent_id = request.POST.get('parent')

            if not name:
                messages.error(request, 'Nome do bairro é obrigatório.')
                return render(request, 'admin_panel/neighborhoods/edit.html', {
                    'neighborhood': neighborhood,
                    'parent_neighborhoods': self._get_parent_neighborhoods(neighborhood),
                })

            parent = get_object_or_404(Neighborhood, id=parent_id) if parent_id else None

            if neighborhood:
                neighborhood.name = name
                neighborhood.parent = parent
                neighborhood.save()
                messages.success(request, 'Bairro atualizado com sucesso!')
            else:
                Neighborhood.objects.create(name=name, parent=parent)
                messages.success(request, 'Bairro criado com sucesso!')

            return redirect('admin_neighborhoods_list')

        except Exception as e:
            messages.error(request, f'Erro ao salvar bairro: {e}')

        return render(request, 'admin_panel/neighborhoods/edit.html', {
            'neighborhood': neighborhood,
            'parent_neighborhoods': self._get_parent_neighborhoods(neighborhood),
        })
