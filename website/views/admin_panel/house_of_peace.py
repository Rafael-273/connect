from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from ..mixins import AdminRequiredMixin
from ...models.house_of_peace import HouseOfPeace, HouseOfPeaceAssignment


class AdminHouseOfPeaceListView(LoginRequiredMixin, AdminRequiredMixin, View):

    def get(self, request):
        search = request.GET.get('search', '').strip()
        status = request.GET.get('status', '')

        qs = HouseOfPeace.objects.annotate(
            active_count=Count(
                'assignments',
                filter=Q(assignments__status__in=['accepted', 'scheduled']),
            )
        ).order_by('-created_at')

        if search:
            qs = qs.filter(
                Q(family_name__icontains=search)
                | Q(address__icontains=search)
                | Q(phone__icontains=search)
            )
        if status:
            qs = qs.filter(status=status)

        page_obj = Paginator(qs, 20).get_page(request.GET.get('page'))

        context = {
            'houses': page_obj,
            'page_obj': page_obj,
            'search': search,
            'status_filter': status,
            'status_choices': HouseOfPeace.STATUS_CHOICES,
            'total': qs.count(),
            'total_available': HouseOfPeace.objects.filter(status='available').count(),
            'total_in_progress': HouseOfPeace.objects.filter(status='in_progress').count(),
            'total_completed': HouseOfPeace.objects.filter(status='completed').count(),
        }
        return render(request, 'admin_panel/house_of_peace/list.html', context)


class AdminHouseOfPeaceDetailView(LoginRequiredMixin, AdminRequiredMixin, View):

    def get(self, request, house_id):
        house = get_object_or_404(
            HouseOfPeace.objects.prefetch_related(
                'assignments__member', 'neighborhood'
            ),
            id=house_id,
        )
        assignments = house.assignments.select_related('member').order_by('-accepted_at')

        context = {
            'house': house,
            'assignments': assignments,
            'status_choices': HouseOfPeace.STATUS_CHOICES,
        }
        return render(request, 'admin_panel/house_of_peace/detail.html', context)


class AdminHouseOfPeaceStatusView(LoginRequiredMixin, AdminRequiredMixin, View):
    """POST — update status of a House of Peace."""

    def post(self, request, house_id):
        house = get_object_or_404(HouseOfPeace, id=house_id)
        new_status = request.POST.get('status', '')
        valid = [c[0] for c in HouseOfPeace.STATUS_CHOICES]
        if new_status not in valid:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': 'Status inválido.'})
            messages.error(request, 'Status inválido.')
            return redirect('admin_house_of_peace_detail', house_id=house_id)

        house.status = new_status
        house.save(update_fields=['status'])

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'status': house.get_status_display()})

        messages.success(request, f'Status atualizado para "{house.get_status_display()}".')
        return redirect('admin_house_of_peace_detail', house_id=house_id)


class AdminHouseOfPeaceDeleteView(LoginRequiredMixin, AdminRequiredMixin, View):

    def post(self, request, house_id):
        house = get_object_or_404(HouseOfPeace, id=house_id)
        house.delete()
        messages.success(request, 'Casa de Paz removida com sucesso.')
        return redirect('admin_house_of_peace_list')
