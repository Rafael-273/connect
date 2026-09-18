from datetime import datetime
import calendar

from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from ...models.event import Event
from ...models.media_content import CONTENT_TYPE_CHOICES, MediaContent
from ...models.media_month_plan import (
    MONTH_NAMES_PT,
    MediaContentCategory,
    MediaMonthPlan,
)
from .mixins import MediaLeaderRequiredMixin, MediaMemberRequiredMixin


class MediaMonthPlanCreateView(MediaLeaderRequiredMixin, View):
    """Form to pick year/month and create a new MediaMonthPlan."""
    template_name = 'member/media_planning/plan_create.html'

    def get(self, request):
        now = timezone.now()
        # Build a list of upcoming months to choose from (current + next 5)
        month_options = []
        y, m = now.year, now.month
        for _ in range(6):
            if not MediaMonthPlan.objects.filter(year=y, month=m).exists():
                month_options.append({'year': y, 'month': m, 'label': f"{MONTH_NAMES_PT[m]} {y}"})
            m += 1
            if m > 12:
                m = 1
                y += 1
        ctx = {**self._nav_context(), 'month_options': month_options}
        return render(request, self.template_name, ctx)

    def post(self, request):
        try:
            year = int(request.POST.get('year', 0))
            month = int(request.POST.get('month', 0))
        except (TypeError, ValueError):
            messages.error(request, 'Mês inválido.')
            return redirect('media_plan_create')
        if not (1 <= month <= 12) or year < 2020:
            messages.error(request, 'Mês/ano inválido.')
            return redirect('media_plan_create')
        plan, created = MediaMonthPlan.objects.get_or_create(year=year, month=month)
        return redirect('media_plan_step', year=year, month=month, step=1)


class MediaMonthPlanDetailView(MediaMemberRequiredMixin, View):
    """Redirect to the appropriate step of the plan."""
    def get(self, request, year, month):
        plan = get_object_or_404(MediaMonthPlan, year=year, month=month)
        return redirect('media_plan_step', year=year, month=month, step=1)


class MediaMonthPlanWizardView(MediaMemberRequiredMixin, View):
    """3-step wizard for month planning."""
    template_name = 'member/media_planning/plan_detail.html'

    def _get_plan(self, year, month):
        return get_object_or_404(MediaMonthPlan, year=year, month=month)

    def get(self, request, year, month, step):
        plan = self._get_plan(year, month)
        step = int(step)
        ctx = self._build_ctx(request, plan, step)
        return render(request, self.template_name, ctx)

    def _build_ctx(self, request, plan, step):
        # Step 1 context: events of this month + all categories
        events_this_month = Event.objects.filter(
            event_date__year=plan.year,
            event_date__month=plan.month,
        ).order_by('event_date')

        plan_event_ids = set(plan.events.values_list('pk', flat=True))
        plan_category_ids = set(plan.categories.values_list('pk', flat=True))
        all_categories = MediaContentCategory.objects.order_by('name')

        # Step 2 context: contents per event and per category
        plan_events = plan.events.order_by('event_date')
        plan_categories = plan.categories.order_by('name')

        # Annotate each event with its content count for this plan
        event_contents = {}
        for ev in plan_events:
            event_contents[ev.pk] = MediaContent.objects.filter(
                month_plan=plan, event=ev
            ).select_related('responsible__member').order_by('due_date', '-created_at')

        category_contents = {}
        for cat in plan_categories:
            category_contents[cat.pk] = MediaContent.objects.filter(
                month_plan=plan, category=cat
            ).select_related('responsible__member').order_by('due_date', '-created_at')

        # Progress for step indicator
        has_macros = plan_events.exists() or plan_categories.exists()
        total_contents = MediaContent.objects.filter(month_plan=plan).count()

        # Attach counts to plan for template header
        plan.event_count = len(plan_event_ids)
        plan.category_count = len(plan_category_ids)

        last_day = calendar.monthrange(plan.year, plan.month)[1]

        return {
            **self._nav_context(),
            'plan': plan,
            'step': step,
            'has_macros': has_macros,
            'total_contents': total_contents,
            # Step 1 data
            'events_this_month': events_this_month,
            'plan_event_ids': plan_event_ids,
            'plan_category_ids': plan_category_ids,
            'all_categories': all_categories,
            'content_type_choices': CONTENT_TYPE_CHOICES,
            'selected_event_count': len(plan_event_ids),
            'selected_category_count': len(plan_category_ids),
            'plan_month_start': f'{plan.year}-{plan.month:02d}-01',
            'plan_month_end': f'{plan.year}-{plan.month:02d}-{last_day:02d}',
            # Step 2 data
            'plan_events': plan_events,
            'plan_categories': plan_categories,
            'event_contents': event_contents,
            'category_contents': category_contents,
        }


class MediaPlanToggleEventView(MediaLeaderRequiredMixin, View):
    """POST: add or remove an event from the plan, redirect back to step 1."""
    def post(self, request, year, month):
        plan = get_object_or_404(MediaMonthPlan, year=year, month=month)
        event_pk = request.POST.get('event_pk')
        event = get_object_or_404(Event, pk=event_pk)
        if plan.events.filter(pk=event.pk).exists():
            plan.events.remove(event)
        else:
            plan.events.add(event)
        return redirect('media_plan_step', year=year, month=month, step=1)


class MediaPlanToggleCategoryView(MediaLeaderRequiredMixin, View):
    """POST: add or remove a category from the plan, redirect back to step 1."""
    def post(self, request, year, month):
        plan = get_object_or_404(MediaMonthPlan, year=year, month=month)
        cat_pk = request.POST.get('category_pk')
        category = get_object_or_404(MediaContentCategory, pk=cat_pk)
        if plan.categories.filter(pk=category.pk).exists():
            plan.categories.remove(category)
        else:
            plan.categories.add(category)
        return redirect('media_plan_step', year=year, month=month, step=1)


class MediaContentCategoryCreateView(MediaLeaderRequiredMixin, View):
    """POST: create a new MediaContentCategory and add it to the plan."""
    def post(self, request, year, month):
        plan = get_object_or_404(MediaMonthPlan, year=year, month=month)
        name = request.POST.get('name', '').strip()
        content_type = request.POST.get('content_type', '')
        description = request.POST.get('description', '').strip()

        valid_types = [c[0] for c in CONTENT_TYPE_CHOICES]
        if not name or content_type not in valid_types:
            messages.error(request, 'Preencha o nome e o tipo da categoria.')
            return redirect('media_plan_step', year=year, month=month, step=1)

        cat, _ = MediaContentCategory.objects.get_or_create(
            name=name,
            defaults={'content_type': content_type, 'description': description},
        )
        plan.categories.add(cat)
        messages.success(request, f'Categoria "{cat.name}" criada!')
        return redirect('media_plan_step', year=year, month=month, step=2)


DEFAULT_EVENT_BANNER = 'event_banners/LOGO_FUNDO.jpg'


class MediaPlanCreateEventView(MediaLeaderRequiredMixin, View):
    """POST: create an event for the plan month and add it to the plan."""
    def post(self, request, year, month):
        plan = get_object_or_404(MediaMonthPlan, year=year, month=month)
        title = request.POST.get('title', '').strip()
        event_date_str = request.POST.get('event_date', '').strip()
        event_time_str = request.POST.get('event_time', '').strip()
        location = request.POST.get('location', '').strip()
        description = request.POST.get('description', '').strip() or title

        if not title or not event_date_str:
            messages.error(request, 'Preencha o título e a data do evento.')
            return redirect('media_plan_step', year=year, month=month, step=1)

        try:
            event_date = datetime.strptime(event_date_str, '%Y-%m-%d').date()
        except ValueError:
            messages.error(request, 'Data inválida.')
            return redirect('media_plan_step', year=year, month=month, step=1)

        if event_date.year != plan.year or event_date.month != plan.month:
            messages.error(
                request,
                f'O evento deve acontecer em {MONTH_NAMES_PT[plan.month]} de {plan.year}.',
            )
            return redirect('media_plan_step', year=year, month=month, step=1)

        event_time = None
        if event_time_str:
            try:
                event_time = datetime.strptime(event_time_str, '%H:%M').time()
            except ValueError:
                messages.error(request, 'Horário inválido.')
                return redirect('media_plan_step', year=year, month=month, step=1)

        event = Event(
            title=title,
            description=description,
            event_date=event_date,
            event_time=event_time,
            location=location,
            display_start=event_date,
            display_end=event_date,
        )
        if 'banner' in request.FILES:
            event.banner = request.FILES['banner']
        else:
            event.banner = DEFAULT_EVENT_BANNER
        event.save()

        plan.events.add(event)
        messages.success(request, f'Evento "{event.title}" criado!')
        return redirect('media_plan_step', year=year, month=month, step=2)


class MediaPlanMacroEventView(MediaMemberRequiredMixin, View):
    """Contents list for an event macro within a month plan."""
    template_name = 'member/media_planning/plan_macro_contents.html'

    def get(self, request, year, month, event_pk):
        plan = get_object_or_404(MediaMonthPlan, year=year, month=month)
        event = get_object_or_404(Event, pk=event_pk)
        if not plan.events.filter(pk=event.pk).exists():
            messages.error(request, 'Este evento não faz parte deste planejamento.')
            return redirect('media_plan_step', year=year, month=month, step=2)

        contents = (
            MediaContent.objects.filter(month_plan=plan, event=event)
            .select_related('responsible__member')
            .order_by('due_date', '-created_at')
        )
        total = contents.count()
        done = contents.filter(status__in=['approved', 'scheduled', 'published']).count()

        ctx = {
            **self._nav_context(),
            'plan': plan,
            'macro_type': 'event',
            'event': event,
            'contents': contents,
            'total': total,
            'done': done,
            'progress': int(done / total * 100) if total else 0,
        }
        return render(request, self.template_name, ctx)


class MediaPlanMacroCategoryView(MediaMemberRequiredMixin, View):
    """Contents list for a category macro within a month plan."""
    template_name = 'member/media_planning/plan_macro_contents.html'

    def get(self, request, year, month, category_pk):
        plan = get_object_or_404(MediaMonthPlan, year=year, month=month)
        category = get_object_or_404(MediaContentCategory, pk=category_pk)
        if not plan.categories.filter(pk=category.pk).exists():
            messages.error(request, 'Esta categoria não faz parte deste planejamento.')
            return redirect('media_plan_step', year=year, month=month, step=2)

        contents = (
            MediaContent.objects.filter(month_plan=plan, category=category)
            .select_related('responsible__member')
            .order_by('due_date', '-created_at')
        )
        total = contents.count()
        done = contents.filter(status__in=['approved', 'scheduled', 'published']).count()

        ctx = {
            **self._nav_context(),
            'plan': plan,
            'macro_type': 'category',
            'category': category,
            'contents': contents,
            'total': total,
            'done': done,
            'progress': int(done / total * 100) if total else 0,
        }
        return render(request, self.template_name, ctx)
