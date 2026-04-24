from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from ..mixins import ConsolidationPermissionMixin
from ...forms.template import (
    FollowUpTemplateForm,
    FollowUpTemplateStepFormSet,
    FollowUpTemplateStepFormSetForCreate,
)
from ...models.follow_up import FollowUp, FollowUpTemplate, FollowUpTemplateStep


class TemplatesListView(LoginRequiredMixin, ConsolidationPermissionMixin, View):
    """Lista os templates de consolidação"""

    def get(self, request):
        search = request.GET.get('search', '')
        category_filter = request.GET.get('category', '')
        status_filter = request.GET.get('status', '')

        templates = FollowUpTemplate.objects.all()
        if search:
            templates = templates.filter(
                Q(name__icontains=search) | Q(description__icontains=search)
            )

        templates = templates.annotate(
            steps_count=Count('steps'), usage_count=Count('followup'),
        ).order_by('-created_at')

        total_templates = FollowUpTemplate.objects.count()
        active_templates = templates.filter(followup__isnull=False).distinct().count()
        total_usage = FollowUp.objects.filter(template__isnull=False).count()

        return render(request, 'admin_panel/templates.html', {
            'templates': templates,
            'search': search,
            'category_filter': category_filter,
            'status_filter': status_filter,
            'stats': {
                'total_templates': total_templates,
                'active_templates': active_templates,
                'total_usage': total_usage,
            },
        })


class TemplateCreateView(LoginRequiredMixin, ConsolidationPermissionMixin, View):
    _TEMPLATE = 'admin_panel/template_form.html'

    def get(self, request):
        original_template = self._get_original_template(request.GET.get('duplicate_from'))
        if original_template:
            form = FollowUpTemplateForm(initial={
                'name': f'Cópia de {original_template.name}',
                'description': original_template.description,
            })
        else:
            form = FollowUpTemplateForm()
        formset = FollowUpTemplateStepFormSetForCreate()
        return render(request, self._TEMPLATE, self._build_context(form, formset, original_template))

    def post(self, request):
        original_template = self._get_original_template(request.POST.get('duplicate_from'))
        form = FollowUpTemplateForm(request.POST)

        if original_template:
            if form.is_valid():
                template = self._duplicate_template(form, original_template)
                messages.success(request, f'Template "{template.name}" criado com sucesso a partir de "{original_template.name}"!')
                return redirect('admin_template_detail', template_id=template.id)
            messages.error(request, 'Erro ao duplicar template. Verifique os dados informados.')
            formset = FollowUpTemplateStepFormSetForCreate()
        else:
            formset = FollowUpTemplateStepFormSetForCreate(request.POST)
            if form.is_valid() and formset.is_valid():
                template = self._create_from_formset(form, formset)
                messages.success(request, f'Template "{template.name}" criado com sucesso!')
                return redirect('admin_template_detail', template_id=template.id)
            messages.error(request, 'Erro ao criar template. Verifique os dados informados.')

        return render(request, self._TEMPLATE, self._build_context(form, formset, original_template))

    @staticmethod
    def _get_original_template(template_id):
        if not template_id:
            return None
        return FollowUpTemplate.objects.filter(id=template_id).first()

    @staticmethod
    def _build_context(form, formset, original_template):
        return {
            'form': form,
            'formset': formset,
            'title': f'Duplicar Template: {original_template.name}' if original_template else 'Novo Template de Consolidação',
            'original_template': original_template,
        }

    @staticmethod
    def _duplicate_template(form, original_template):
        template = form.save(commit=False)
        template.description = original_template.description
        template.save()
        for step in original_template.steps.all():
            FollowUpTemplateStep.objects.create(
                template=template, period=step.period,
                period_type=step.period_type, title=step.title,
                description=step.description,
            )
        return template

    @staticmethod
    def _create_from_formset(form, formset):
        template = form.save()
        formset.instance = template
        formset.save()
        return template


class TemplateEditView(LoginRequiredMixin, ConsolidationPermissionMixin, View):
    """Editar template de consolidação"""

    def get(self, request, template_id):
        template = get_object_or_404(FollowUpTemplate, id=template_id)
        form = FollowUpTemplateForm(instance=template)
        formset = FollowUpTemplateStepFormSet(instance=template)
        return render(request, 'admin_panel/template_form.html', {
            'form': form, 'formset': formset,
            'template': template, 'title': f'Editar Template: {template.name}',
        })

    def post(self, request, template_id):
        template = get_object_or_404(FollowUpTemplate, id=template_id)
        form = FollowUpTemplateForm(request.POST, instance=template)
        formset = FollowUpTemplateStepFormSet(request.POST, instance=template)

        if form.is_valid() and formset.is_valid():
            template = form.save()
            formset.save()
            messages.success(request, f'Template "{template.name}" atualizado com sucesso!')
            return redirect('admin_template_detail', template_id=template.id)

        messages.error(request, 'Erro ao atualizar template. Verifique os dados informados.')
        return render(request, 'admin_panel/template_form.html', {
            'form': form, 'formset': formset,
            'template': template, 'title': f'Editar Template: {template.name}',
        })


class TemplateDetailView(LoginRequiredMixin, ConsolidationPermissionMixin, View):
    """Visualizar detalhes do template"""

    def get(self, request, template_id):
        template = get_object_or_404(FollowUpTemplate, id=template_id)
        steps = template.steps.all().order_by('week')
        followups_using = FollowUp.objects.filter(template=template)
        return render(request, 'admin_panel/template_detail.html', {
            'template': template, 'steps': steps,
            'followups_using': followups_using,
            'usage_count': followups_using.count(),
        })


class TemplateDeleteView(LoginRequiredMixin, ConsolidationPermissionMixin, View):
    """Excluir template de consolidação"""

    def get(self, request, template_id):
        template = get_object_or_404(FollowUpTemplate, id=template_id)
        followups_using = FollowUp.objects.filter(template=template)
        return render(request, 'admin_panel/template_delete.html', {
            'template': template, 'followups_using': followups_using,
        })

    def post(self, request, template_id):
        template = get_object_or_404(FollowUpTemplate, id=template_id)
        template_name = template.name
        template.delete()
        messages.success(request, f'Template "{template_name}" excluído com sucesso!')
        return redirect('admin_templates')
