from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from ..mixins import ModulePermissionMixin
from ...forms.follow_up import FollowUpForm, FollowUpReportForm
from ...models.follow_up import FollowUp, FollowUpReport, FollowUpTemplate
from ...models.member import Member


class FollowUpListView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Listar follow-ups"""
    module_name = 'consolidation'

    def _get_queryset(self):
        return FollowUp.objects.select_related('accompanied', 'responsible').order_by('-created_at')

    def _apply_filters(self, qs, search, responsible_filter, status_filter):
        if search:
            qs = qs.filter(
                Q(accompanied__name__icontains=search)
                | Q(responsible__name__icontains=search)
            )
        if responsible_filter:
            qs = qs.filter(responsible_id=responsible_filter)
        if status_filter == 'active':
            qs = qs.filter(end_date__isnull=True)
        elif status_filter == 'finished':
            qs = qs.filter(end_date__isnull=False)
        return qs

    def _build_context(self, request):
        search = request.GET.get('search', '').strip()
        responsible_filter = request.GET.get('responsible', '')
        status_filter = request.GET.get('status', '')
        qs = self._apply_filters(self._get_queryset(), search, responsible_filter, status_filter)
        return {
            'followups': qs,
            'search': search,
            'responsible_filter': responsible_filter,
            'status_filter': status_filter,
            'responsibles': Member.objects.filter(
                is_active=True, is_available_to_consolidate=True,
            ).order_by('name'),
        }

    def get(self, request):
        return render(request, 'admin_panel/followups/list.html', self._build_context(request))


class FollowUpEditView(LoginRequiredMixin, ModulePermissionMixin, View):
    module_name = 'consolidation'
    _TEMPLATE = 'admin_panel/followups/edit.html'

    def get(self, request, followup_id=None):
        followup = self._get_followup(followup_id)
        form = FollowUpForm(instance=followup)
        return render(request, self._TEMPLATE, {
            'form': form,
            'followup': followup,
            'templates_data': self._get_templates_data(),
        })

    def post(self, request, followup_id=None):
        followup = self._get_followup(followup_id)
        form = FollowUpForm(request.POST, instance=followup)
        if form.is_valid():
            form.save()
            messages.success(request, 'Follow-up salvo com sucesso!')
            return redirect('admin_followups_list')

        messages.error(request, 'Erro ao salvar follow-up. Verifique os dados.')
        return render(request, self._TEMPLATE, {
            'form': form,
            'followup': followup,
            'templates_data': self._get_templates_data(),
        })

    @staticmethod
    def _get_followup(followup_id):
        if not followup_id:
            return None
        return get_object_or_404(FollowUp, id=followup_id)

    @staticmethod
    def _get_templates_data():
        rows = FollowUpTemplate.objects.annotate(
            steps_count_val=Count('steps'),
        ).values_list('id', 'name', 'description', 'steps_count_val')
        return [
            {'id': t[0], 'name': t[1], 'description': t[2] or 'Sem descrição', 'steps_count': t[3]}
            for t in rows
        ]


class FollowUpDeleteView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Deletar follow-up"""
    module_name = 'consolidation'

    def get(self, request, followup_id):
        followup = get_object_or_404(FollowUp, id=followup_id)
        return render(request, 'admin_panel/followups/delete.html', {'followup': followup})

    def post(self, request, followup_id):
        followup = get_object_or_404(FollowUp, id=followup_id)
        try:
            followup.delete()

            if request.headers.get('Content-Type') == 'application/json':
                return JsonResponse({'success': True, 'message': 'Consolidação excluída com sucesso!'})

            messages.success(request, 'Follow-up excluído com sucesso!')
            return redirect('admin_followups_list')
        except Exception as e:
            if request.headers.get('Content-Type') == 'application/json':
                return JsonResponse({'success': False, 'error': str(e)}, status=400)

            messages.error(request, f'Erro ao excluir follow-up: {e}')
            return redirect('admin_followups_list')


class FollowUpReportView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Adicionar relatório ao follow-up"""
    module_name = 'consolidation'

    def get(self, request, followup_id):
        followup = get_object_or_404(FollowUp, id=followup_id)
        form = FollowUpReportForm()
        return render(request, 'admin_panel/followups/report.html', {'form': form, 'followup': followup})

    def post(self, request, followup_id):
        followup = get_object_or_404(FollowUp, id=followup_id)
        form = FollowUpReportForm(request.POST)
        if form.is_valid():
            report = form.save(commit=False)
            report.followup = followup
            report.save()
            messages.success(request, 'Relatório adicionado com sucesso!')
            return redirect('admin_followup_detail', followup_id=followup.id)

        messages.error(request, 'Erro ao salvar relatório. Verifique os dados.')
        return render(request, 'admin_panel/followups/report.html', {'form': form, 'followup': followup})


class FollowUpDetailView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Detalhes do follow-up com relatórios"""
    module_name = 'consolidation'

    def get(self, request, followup_id):
        followup = get_object_or_404(FollowUp, id=followup_id)
        reports = FollowUpReport.objects.filter(followup=followup).order_by('-date')
        return render(request, 'admin_panel/followups/detail.html', {
            'followup': followup, 'reports': reports,
        })
