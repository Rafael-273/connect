import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Avg, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from ..mixins import AdminRequiredMixin, ModulePermissionMixin
from ...forms.canteen import CanteenDebtorForm
from ...models.canteen import CanteenDebtor


class CanteenListView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Lista todos os fiados registrados na cantina"""
    module_name = 'canteen'

    def _get_queryset(self):
        return CanteenDebtor.objects.all()

    def _apply_filters(self, qs, search, status_filter):
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(phone__icontains=search)
                | Q(description__icontains=search)
            )
        if status_filter == 'paid':
            qs = qs.filter(paid=True)
        elif status_filter == 'unpaid':
            qs = qs.filter(paid=False)
        return qs.order_by('-purchase_date')

    def _get_stats(self):
        unpaid_qs = CanteenDebtor.objects.filter(paid=False)
        paid_qs = CanteenDebtor.objects.filter(paid=True)
        return {
            'total_debtors': CanteenDebtor.objects.count(),
            'total_unpaid': unpaid_qs.count(),
            'total_amount_unpaid': unpaid_qs.aggregate(total=Sum('amount'))['total'] or 0,
            'total_amount_paid': paid_qs.aggregate(total=Sum('amount'))['total'] or 0,
            'avg_debt_amount': CanteenDebtor.objects.aggregate(avg=Avg('amount'))['avg'] or 0,
            'recent_payments': paid_qs.filter(
                paid_date__gte=timezone.now() - timedelta(days=30),
            ).count(),
        }

    def _build_context(self, request):
        search = request.GET.get('search', '')
        status_filter = request.GET.get('status', '')
        qs = self._apply_filters(self._get_queryset(), search, status_filter)
        debtors = Paginator(qs, 10).get_page(request.GET.get('page', 1))
        return {
            'debtors': debtors,
            'search': search,
            'status_filter': status_filter,
            **self._get_stats(),
        }

    def get(self, request):
        return render(request, 'admin_panel/cantina/list.html', self._build_context(request))


class CanteenEditView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Cria ou edita um registro de fiado da cantina"""
    module_name = 'canteen'

    def get(self, request, debtor_id=None):
        debtor = get_object_or_404(CanteenDebtor, id=debtor_id) if debtor_id else None
        form = CanteenDebtorForm(instance=debtor)
        return render(request, 'admin_panel/cantina/edit.html', {
            'form': form, 'debtor': debtor, 'is_edit': debtor_id is not None,
        })

    def post(self, request, debtor_id=None):
        debtor = get_object_or_404(CanteenDebtor, id=debtor_id) if debtor_id else None
        form = CanteenDebtorForm(request.POST, instance=debtor)
        if form.is_valid():
            form.save()
            action = 'atualizado' if debtor_id else 'criado'
            messages.success(request, f'Registro de fiado {action} com sucesso!')
            return redirect('admin_cantina_list')
        return render(request, 'admin_panel/cantina/edit.html', {
            'form': form, 'debtor': debtor, 'is_edit': debtor_id is not None,
        })


class CanteenDetailView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Exibe os detalhes de um registro de fiado da cantina"""

    def get(self, request, debtor_id):
        debtor = get_object_or_404(CanteenDebtor, id=debtor_id)
        return render(request, 'admin_panel/cantina/detail.html', {'debtor': debtor})


class CanteenDeleteView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Exclui um registro de fiado da cantina"""

    def get(self, request, debtor_id):
        debtor = get_object_or_404(CanteenDebtor, id=debtor_id)
        return render(request, 'admin_panel/cantina/delete.html', {'debtor': debtor})

    def post(self, request, debtor_id):
        debtor = get_object_or_404(CanteenDebtor, id=debtor_id)
        debtor.delete()
        messages.success(request, 'Registro de fiado excluído com sucesso!')
        return redirect('admin_cantina_list')


class CanteenTogglePaidView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Toggle do status de pagamento de um fiado"""

    def post(self, request, debtor_id):
        debtor = get_object_or_404(CanteenDebtor, id=debtor_id)
        debtor.paid = not debtor.paid
        debtor.paid_date = timezone.now().date() if debtor.paid else None
        debtor.save()

        status = 'pago' if debtor.paid else 'não pago'
        messages.success(request, f'Status alterado para {status} com sucesso!')
        return redirect('admin_cantina_list')


@method_decorator(csrf_exempt, name='dispatch')
class CanteenApiView(View):
    """API para operações CRUD da cantina via AJAX"""

    def post(self, request):
        try:
            data = json.loads(request.body)

            if 'id' in data:
                debtor = get_object_or_404(CanteenDebtor, id=data['id'])
                for field in ('name', 'phone', 'purchase_date', 'amount', 'description', 'paid', 'notes'):
                    if field in data:
                        setattr(debtor, field, data[field])
                if 'paid' in data:
                    debtor.paid = data['paid']
                    debtor.paid_date = timezone.now().date() if debtor.paid else None
                debtor.save()
                message = 'Registro atualizado com sucesso!'
            else:
                debtor = CanteenDebtor.objects.create(
                    name=data.get('name'),
                    phone=data.get('phone'),
                    purchase_date=data.get('purchase_date', timezone.now().date()),
                    amount=data.get('amount'),
                    description=data.get('description', ''),
                    paid=data.get('paid', False),
                    notes=data.get('notes', ''),
                )
                if debtor.paid:
                    debtor.paid_date = timezone.now().date()
                    debtor.save()
                message = 'Novo registro criado com sucesso!'

            return JsonResponse({'success': True, 'message': message, 'id': debtor.id})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
