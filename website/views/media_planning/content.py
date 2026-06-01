from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView

from ...models.event import Event
from ...models.media_content import CONTENT_TYPE_CHOICES, STATUS_CHOICES, MediaContent
from ...models.media_task import TASK_STATUS_CHOICES, MediaTask
from ...models.user import User
from ...forms.media_planning import (
    MediaAttachmentForm,
    MediaCommentForm,
    MediaContentForm,
    MediaTaskForm,
)
from .mixins import MediaLeaderRequiredMixin, MediaMemberRequiredMixin


class MediaContentListView(MediaMemberRequiredMixin, ListView):
    model = MediaContent
    template_name = 'member/media_planning/content_list.html'
    context_object_name = 'contents'
    paginate_by = 20

    def get_queryset(self):
        qs = (
            MediaContent.objects.select_related('event', 'responsible__member')
            .order_by('-created_at')
        )
        event_id = self.request.GET.get('event', '')
        content_type = self.request.GET.get('content_type', '')
        responsible_id = self.request.GET.get('responsible', '')
        status = self.request.GET.get('status', '')
        pub_date = self.request.GET.get('pub_date', '')

        if event_id:
            qs = qs.filter(event_id=event_id)
        if content_type:
            qs = qs.filter(content_type=content_type)
        if responsible_id:
            qs = qs.filter(responsible_id=responsible_id)
        if status:
            qs = qs.filter(status=status)
        if pub_date:
            qs = qs.filter(publication_date__date=pub_date)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['events'] = Event.objects.order_by('-event_date')[:50]
        ctx['users'] = (
            User.objects.filter(member__isnull=False)
            .select_related('member')
            .order_by('member__name')
        )
        ctx['content_types'] = CONTENT_TYPE_CHOICES
        ctx['status_choices'] = STATUS_CHOICES
        ctx['filter_event'] = self.request.GET.get('event', '')
        ctx['filter_content_type'] = self.request.GET.get('content_type', '')
        ctx['filter_responsible'] = self.request.GET.get('responsible', '')
        ctx['filter_status'] = self.request.GET.get('status', '')
        ctx['filter_pub_date'] = self.request.GET.get('pub_date', '')
        return ctx


class MediaContentDetailView(MediaMemberRequiredMixin, View):
    template_name = 'member/media_planning/content_detail.html'

    def get(self, request, pk):
        content = get_object_or_404(MediaContent, pk=pk)
        ctx = {
            **self._nav_context(),
            'content': content,
            'tasks': content.tasks.select_related('assigned_to__member').order_by('due_date'),
            'comments': content.comments.select_related('author__member').order_by('created_at'),
            'attachments': content.attachments.select_related('uploaded_by__member').order_by('-created_at'),
            'task_form': MediaTaskForm(),
            'comment_form': MediaCommentForm(),
            'attachment_form': MediaAttachmentForm(),
            'task_statuses': TASK_STATUS_CHOICES,
            'tab': request.GET.get('tab', 'details'),
        }
        return render(request, self.template_name, ctx)


class MediaContentCreateView(MediaLeaderRequiredMixin, View):
    template_name = 'member/media_planning/content_form.html'

    def get(self, request):
        initial = {}
        month_plan_pk = request.GET.get('month_plan', '')
        event_pk = request.GET.get('event', '')
        category_pk = request.GET.get('category', '')  # MediaContentCategory PK
        content_type = request.GET.get('content_type', '')

        if event_pk:
            initial['event'] = event_pk
        if content_type:
            initial['content_type'] = content_type
        if category_pk:
            try:
                from ...models.media_month_plan import MediaContentCategory
                cat = MediaContentCategory.objects.get(pk=category_pk)
                initial['content_type'] = cat.content_type
            except Exception:
                pass

        ctx = {
            **self._nav_context(),
            'form': MediaContentForm(initial=initial),
            'action': 'create',
            '_month_plan_pk': month_plan_pk,
            '_category_pk': category_pk,
            'prefill_event': event_pk,
        }
        return render(request, self.template_name, ctx)

    def post(self, request):
        form = MediaContentForm(request.POST)
        if form.is_valid():
            content = form.save(commit=False)
            month_plan_pk = request.POST.get('_month_plan_pk', '')
            category_pk = request.POST.get('_category_pk', '')
            if month_plan_pk:
                from ...models.media_month_plan import MediaMonthPlan
                try:
                    content.month_plan = MediaMonthPlan.objects.get(pk=month_plan_pk)
                except MediaMonthPlan.DoesNotExist:
                    pass
            if category_pk:
                from ...models.media_month_plan import MediaContentCategory
                try:
                    content.category = MediaContentCategory.objects.get(pk=category_pk)
                except MediaContentCategory.DoesNotExist:
                    pass
            content.save()
            messages.success(request, f'Conteúdo "{content.title}" criado com sucesso!')
            if content.month_plan_id:
                plan = content.month_plan
                return redirect('media_plan_step', year=plan.year, month=plan.month, step=2)
            if content.event_id:
                return redirect('media_event_contents', event_pk=content.event_id)
            return redirect('media_category_contents', content_type=content.content_type)
        ctx = {
            **self._nav_context(),
            'form': form,
            'action': 'create',
            '_month_plan_pk': request.POST.get('_month_plan_pk', ''),
            '_category_pk': request.POST.get('_category_pk', ''),
        }
        return render(request, self.template_name, ctx)


class MediaContentUpdateView(MediaLeaderRequiredMixin, View):
    template_name = 'member/media_planning/content_form.html'

    def get(self, request, pk):
        content = get_object_or_404(MediaContent, pk=pk)
        ctx = {
            **self._nav_context(),
            'form': MediaContentForm(instance=content),
            'content': content,
            'action': 'edit',
        }
        return render(request, self.template_name, ctx)

    def post(self, request, pk):
        content = get_object_or_404(MediaContent, pk=pk)
        form = MediaContentForm(request.POST, instance=content)
        if form.is_valid():
            content = form.save()
            messages.success(request, f'Conteúdo "{content.title}" atualizado!')
            return redirect('media_content_detail', pk=content.pk)
        ctx = {
            **self._nav_context(),
            'form': form,
            'content': content,
            'action': 'edit',
        }
        return render(request, self.template_name, ctx)


class MediaContentDeleteView(MediaLeaderRequiredMixin, View):
    def post(self, request, pk):
        content = get_object_or_404(MediaContent, pk=pk)
        title = content.title
        content.delete()
        messages.success(request, f'Conteúdo "{title}" excluído.')
        return redirect('media_content_list')


class MediaTaskCreateView(MediaLeaderRequiredMixin, View):
    def post(self, request, pk):
        content = get_object_or_404(MediaContent, pk=pk)
        form = MediaTaskForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
            task.content = content
            task.save()
            messages.success(request, f'Tarefa "{task.title}" criada!')
        else:
            messages.error(request, 'Erro ao criar a tarefa. Verifique os campos.')
        return redirect('media_content_detail', pk=pk)


class MediaTaskUpdateStatusView(MediaMemberRequiredMixin, View):
    def post(self, request, pk):
        task = get_object_or_404(MediaTask, pk=pk)
        new_status = request.POST.get('status', '')
        valid_statuses = [c[0] for c in TASK_STATUS_CHOICES]
        if new_status in valid_statuses:
            task.status = new_status
            task.save(update_fields=['status', 'update_at'])
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'status': new_status, 'label': task.get_status_display()})
            messages.success(
                request,
                f'Status atualizado para "{task.get_status_display()}".',
            )
        return redirect('media_content_detail', pk=task.content_id)


class MediaCommentCreateView(MediaMemberRequiredMixin, View):
    def post(self, request, pk):
        content = get_object_or_404(MediaContent, pk=pk)
        from ...forms.media_planning import MediaCommentForm as _Form
        form = _Form(request.POST)
        if form.is_valid():
            comment = form.save(commit=False)
            comment.author = request.user
            comment.content = content
            comment.save()
        else:
            messages.error(request, 'Não foi possível adicionar o comentário.')
        return redirect('media_content_detail', pk=pk)


class MediaAttachmentCreateView(MediaMemberRequiredMixin, View):
    def post(self, request, pk):
        content = get_object_or_404(MediaContent, pk=pk)
        from ...forms.media_planning import MediaAttachmentForm as _Form
        form = _Form(request.POST, request.FILES)
        if form.is_valid():
            attachment = form.save(commit=False)
            attachment.content = content
            attachment.uploaded_by = request.user
            attachment.save()
            messages.success(request, f'Arquivo "{attachment.name}" anexado!')
        else:
            messages.error(request, 'Erro ao enviar o arquivo. Verifique o tipo e tamanho.')
        return redirect('media_content_detail', pk=pk)
