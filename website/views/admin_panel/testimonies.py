import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from ..mixins import AdminRequiredMixin
from ...models.testimony import Testimony


class TestimonyListView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Lista de testemunhos com filtros"""

    def _get_queryset(self):
        return Testimony.objects.select_related('member').order_by('-created_at')

    def _apply_filters(self, qs, search, category, approved):
        if search:
            qs = qs.filter(Q(title__icontains=search))
        if category:
            qs = qs.filter(category=category)
        if approved == 'true':
            qs = qs.filter(is_approved=True)
        elif approved == 'false':
            qs = qs.filter(is_approved=False)
        return qs

    def _build_context(self, request):
        search = request.GET.get('search', '').strip()
        category = request.GET.get('category', '')
        approved = request.GET.get('approved', '')
        qs = self._apply_filters(self._get_queryset(), search, category, approved)
        page_obj = Paginator(qs, 12).get_page(request.GET.get('page'))
        return {
            'testimonies': page_obj,
            'page_obj': page_obj,
            'category_choices': Testimony.CATEGORY_CHOICES,
            'total': qs.count(),
            'total_approved': Testimony.objects.filter(is_approved=True).count(),
            'total_pending': Testimony.objects.filter(is_approved=False).count(),
            'search': search,
            'category': category,
            'approved': approved,
        }

    def get(self, request):
        return render(request, 'admin_panel/testimonies/list.html', self._build_context(request))


class TestimonyEditView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Criar ou editar testemunho"""

    def _get_context(self, testimony=None):
        return {
            'testimony': testimony,
            'category_choices': Testimony.CATEGORY_CHOICES,
        }

    def get(self, request, testimony_id=None):
        testimony = get_object_or_404(Testimony, id=testimony_id) if testimony_id else None
        storage = messages.get_messages(request)
        storage.used = True
        return render(request, 'admin_panel/testimonies/edit.html', self._get_context(testimony))

    def post(self, request, testimony_id=None):
        testimony = get_object_or_404(Testimony, id=testimony_id) if testimony_id else None
        try:
            title = request.POST.get('title', '').strip()
            category = request.POST.get('category', 'other')
            is_approved = request.POST.get('is_approved') == 'on'
            show_on_home = request.POST.get('show_on_home') == 'on'
            instagram_url = request.POST.get('instagram_url', '').strip() or None

            if not title:
                messages.error(request, 'O título do testemunho é obrigatório.')
                return render(request, 'admin_panel/testimonies/edit.html', self._get_context(testimony))

            if testimony:
                testimony.title = title
                testimony.category = category
                testimony.is_approved = is_approved
                testimony.show_on_home = show_on_home
                testimony.instagram_url = instagram_url
                testimony.save()
                messages.success(request, 'Testemunho atualizado com sucesso!')
            else:
                Testimony.objects.create(
                    author_name='',
                    title=title,
                    category=category,
                    is_approved=is_approved,
                    show_on_home=show_on_home,
                    instagram_url=instagram_url,
                )
                messages.success(request, 'Testemunho cadastrado com sucesso!')

            return redirect('admin_testimonies_list')

        except Exception as e:
            messages.error(request, f'Erro ao salvar testemunho: {str(e)}')

        return render(request, 'admin_panel/testimonies/edit.html', self._get_context(testimony))


class TestimonyDeleteView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Deletar testemunho"""

    def get(self, request, testimony_id):
        testimony = get_object_or_404(Testimony, id=testimony_id)
        return render(request, 'admin_panel/testimonies/delete.html', {'testimony': testimony})

    def post(self, request, testimony_id):
        testimony = get_object_or_404(Testimony, id=testimony_id)
        name = testimony.title
        testimony.delete()
        messages.success(request, f'Testemunho "{name}" excluído com sucesso!')
        return redirect('admin_testimonies_list')


@method_decorator(csrf_exempt, name='dispatch')
class TestimonyToggleView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Alterna aprovação ou exibição na home via AJAX"""

    def post(self, request, testimony_id):
        testimony = get_object_or_404(Testimony, id=testimony_id)
        data = json.loads(request.body)
        field = data.get('field')  # 'is_approved' or 'show_on_home'
        if field == 'is_approved':
            testimony.is_approved = not testimony.is_approved
            testimony.save(update_fields=['is_approved'])
            return JsonResponse({'success': True, 'value': testimony.is_approved})
        elif field == 'show_on_home':
            testimony.show_on_home = not testimony.show_on_home
            testimony.save(update_fields=['show_on_home'])
            return JsonResponse({'success': True, 'value': testimony.show_on_home})
        return JsonResponse({'success': False, 'error': 'Requisição inválida'})
