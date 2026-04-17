import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from ..mixins import AdminRequiredMixin
from ...models.event import Event
from ...models.member import Member
from ...models.ministry import Ministry
from ...models.neighborhood import Neighborhood
from ...models.visitor import Visitor


@method_decorator(csrf_exempt, name='dispatch')
class ApiDeleteItemView(LoginRequiredMixin, AdminRequiredMixin, View):
    """API para deletar itens via AJAX"""

    MODEL_MAP = {
        'member': Member,
        'visitor': Visitor,
        'event': Event,
        'ministry': Ministry,
        'neighborhood': Neighborhood,
    }

    def post(self, request):
        try:
            data = json.loads(request.body)
            model_class = self.MODEL_MAP.get(data.get('model'))
            if not model_class:
                return JsonResponse({'success': False, 'error': 'Modelo inválido'})

            item = get_object_or_404(model_class, id=data.get('id'))
            item.delete()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
