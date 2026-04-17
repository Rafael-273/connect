from datetime import date
from django.contrib.auth import get_user_model
from django.views.generic.edit import CreateView
from django.views.generic import ListView, View
from django.urls import reverse_lazy
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.core.files.base import ContentFile
import base64
from ..models.member import Member
from ..models.follow_up import FollowUp, FollowUpTemplate
from ..forms.member import MemberForm
from ..forms.follow_up import FollowUpReportForm
from .mixins import MemberRequiredMixin, MinistrationContextMixin

User = get_user_model()


class MemberCreateView(CreateView):
    model = Member
    form_class = MemberForm
    template_name = 'create/member.html'
    success_url = reverse_lazy('member_login')

    def form_valid(self, form):
        email = form.cleaned_data.get('email')
        password = form.cleaned_data.get('password')

        error = self._validate_email(email)
        if error:
            messages.error(self.request, error)
            return self.form_invalid(form)

        try:
            user = self._create_user(email, password)
            member = form.save(commit=False)
            member.user = user
            member.profile_picture = self._decode_cropped_image(self.request.POST.get('cropped_image_data'), user.id)
            member.save()
            self.object = member
            messages.success(self.request, 'Cadastro realizado com sucesso! Você já pode fazer login.')
            return super().form_valid(form)
        except Exception as e:
            messages.error(self.request, f'Erro ao realizar cadastro: {str(e)}')
            return self.form_invalid(form)

    def _validate_email(self, email):
        """Returns an error message string or None."""
        if not email:
            return 'O e-mail é obrigatório.'
        if User.objects.filter(email=email).exists():
            return 'Este e-mail já está cadastrado no sistema. Por favor, use outro e-mail.'
        return None

    def _create_user(self, email, password):
        return User.objects.create_user(email=email, password=password)

    def _decode_cropped_image(self, raw_data, user_id):
        """Decodes a base64 data-URL into a ContentFile, or returns None."""
        if not raw_data:
            return None
        if ',' in raw_data:
            header, imgstr = raw_data.split(';base64,')
            ext = header.split('/')[-1]
        else:
            imgstr, ext = raw_data, 'jpg'
        return ContentFile(base64.b64decode(imgstr), name=f'profile_{user_id}.{ext}')
    

class NewConvertsListView(ListView):
    model = Member
    template_name = 'list/new_converts.html'
    context_object_name = 'members'

    def get_queryset(self):
        queryset = (
            super().get_queryset()
            .filter(conversion__in=['new_convert', 'reconciliation'])
            .order_by('-created_at')
        )
        query = self._get_search_query()
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query) | Q(phone__icontains=query)
            )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['members'] = self._enrich_members(context['members'])
        context['query'] = self._get_search_query()
        return context

    # ── Helpers ────────────────────────────────────────────────────────

    def _get_search_query(self):
        return self.request.GET.get('q', '')

    def _enrich_members(self, members):
        """Annotates each member with cleaned_phone and first_name in place."""
        for member in members:
            member.cleaned_phone = self._normalize_phone(member.phone)
            if member.name:
                member.name = member.name.title()
                member.first_name = member.name.split()[0]
            else:
                member.first_name = ''
        return members

    @staticmethod
    def _normalize_phone(phone, default_ddd='21'):
        import re
        raw = re.sub(r'\D', '', phone or '')
        if len(raw) in (8, 9):
            return default_ddd + raw
        if len(raw) in (10, 11):
            return raw
        if raw.startswith('55') and len(raw) >= 12:
            return raw[2:]
        return raw


class MemberConsolidationListView(MemberRequiredMixin, MinistrationContextMixin, View):
    def get(self, request):
        member = self.member
        followups = self._get_followups(member)
        return render(request, 'member/consolidation_list.html', self._build_context(member, followups))

    def _get_followups(self, member):
        return (
            FollowUp.objects
            .filter(responsible=member, is_active=True)
            .select_related('accompanied', 'template')
            .prefetch_related('reports')
            .order_by('-created_at')
        )

    def _build_context(self, member, followups):
        return {
            'followups': followups,
            'can_consolidate': member.is_available_to_consolidate,
            'is_ministration_member': self.get_ministration_status(member),
        }


class MemberConsolidationDetailView(MemberRequiredMixin, MinistrationContextMixin, View):
    """Detalhes de um consolidado específico - mostra dicas do período atual"""

    def get(self, request, followup_id):
        member = self.member
        followup = get_object_or_404(
            FollowUp.objects.select_related('accompanied', 'responsible', 'template'),
            id=followup_id,
            responsible=member
        )

        current_step = followup.current_step
        reports = followup.reports.all().order_by('-date')[:10]
        can_submit_report = not followup.reports.filter(week=followup.current_week).exists()

        context = {
            'followup': followup,
            'current_step': current_step,
            'reports': reports,
            'can_submit_report': can_submit_report,
            'period_name': "Semana",
            'is_ministration_member': self.get_ministration_status(member),
        }
        return render(request, 'member/consolidation_detail.html', context)


class MemberConsolidationReportView(MemberRequiredMixin, MinistrationContextMixin, View):
    """Formulário para enviar relatório do período"""

    def _get_followup(self):
        return get_object_or_404(
            FollowUp.objects.select_related('accompanied', 'template'),
            id=self.kwargs['followup_id'],
            responsible=self.member
        )

    def get(self, request, followup_id):
        followup = self._get_followup()

        if followup.reports.filter(week=followup.current_week).exists():
            messages.warning(request, "Você já enviou o relatório para esta semana.")
            return redirect('member_consolidation_detail', followup_id=followup.id)

        form = FollowUpReportForm(followup=followup)
        return render(request, 'member/consolidation_report.html', self._build_context(followup, form))

    def post(self, request, followup_id):
        followup = self._get_followup()

        if followup.reports.filter(week=followup.current_week).exists():
            messages.warning(request, "Você já enviou o relatório para esta semana.")
            return redirect('member_consolidation_detail', followup_id=followup.id)

        form = FollowUpReportForm(request.POST, followup=followup)
        if form.is_valid():
            report = form.save(commit=False)
            report.followup = followup
            report.save()
            messages.success(request, f"Relatório da semana {report.week} enviado com sucesso!")
            return redirect('member_consolidation_detail', followup_id=followup.id)

        return render(request, 'member/consolidation_report.html', self._build_context(followup, form))

    def _build_context(self, followup, form):
        return {
            'followup': followup,
            'form': form,
            'current_step': followup.current_step,
            'period_name': "Semana",
            'is_ministration_member': self.get_ministration_status(self.member),
        }


class ConsolidatorGuideView(MemberRequiredMixin, MinistrationContextMixin, View):
    def get(self, request):
        member = self.member
        context = {
            'member': member,
        }
        return render(request, 'member/consolidator_guide.html', context)


class ConsolidatorAssignmentsView(MemberRequiredMixin, MinistrationContextMixin, View):
    def get(self, request):
        member = self.member
        age = self._get_member_age(member)
        available = self._get_available_people(member, age)
        my_count = FollowUp.objects.filter(responsible=member, is_active=True).count()
        return render(request, 'member/consolidator_assignments.html',
                      self._build_context(member, available, my_count, age))

    @staticmethod
    def _get_member_age(member):
        if not member.birth_date:
            return None
        today = date.today()
        return today.year - member.birth_date.year - (
            (today.month, today.day) < (member.birth_date.month, member.birth_date.day)
        )

    def _get_available_people(self, member, age):
        """Returns people eligible to be consolidated by this member."""
        already_accompanied = FollowUp.objects.filter(
            is_active=True
        ).values_list('accompanied_id', flat=True)

        qs = (
            Member.objects
            .filter(conversion__in=['new_convert', 'reconciled'], is_consolidated=False, is_active=True)
            .exclude(id=member.id)
            .exclude(id__in=already_accompanied)
        )

        if member.gender:
            qs = qs.filter(gender=member.gender)

        qs = self._apply_age_filter(qs, age)

        return qs.distinct().order_by('-conversion_date', '-created_at')

    @staticmethod
    def _apply_age_filter(qs, age):
        """Filters queryset to people within an appropriate age range."""
        if not age:
            return qs
        today = date.today()
        if age >= 50:
            return qs.filter(birth_date__lte=date(today.year - 40, today.month, today.day))
        return qs.filter(
            birth_date__year__gte=today.year - (age + 10),
            birth_date__year__lte=today.year - (age - 10),
        )

    def _build_context(self, member, available_people, my_count, age):
        return {
            'available_people': available_people,
            'my_consolidations_count': my_count,
            'can_consolidate': member.is_available_to_consolidate,
            'consolidator_age': age,
        }


class RequestConsolidationView(MemberRequiredMixin, View):
    """Criar uma nova consolidação automaticamente"""

    def post(self, request, person_id):
        member = self.member
        person = get_object_or_404(Member, id=person_id)

        error = self._validate_request(member, person)
        if error:
            return JsonResponse({'error': error['message']}, status=error['status'])

        followup = self._create_followup(member, person)
        return JsonResponse({
            'success': True,
            'message': 'Consolidação iniciada com sucesso!',
            'followup_id': followup.id,
        })

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({'error': 'Método não permitido'}, status=405)

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _validate_request(member, person):
        """Returns a dict with 'message' and 'status', or None if valid."""
        if not member.is_available_to_consolidate:
            return {'message': 'Você não está disponível para consolidar no momento.', 'status': 403}
        if FollowUp.objects.filter(accompanied=person, is_active=True).exists():
            return {'message': 'Esta pessoa já está sendo consolidada.', 'status': 400}
        return None

    @staticmethod
    def _create_followup(member, person):
        template = FollowUpTemplate.objects.order_by('-created_at').first()
        return FollowUp.objects.create(
            responsible=member,
            accompanied=person,
            template=template,
            is_active=True,
            profile_notes=f'Consolidação iniciada em {date.today().strftime("%d/%m/%Y")}',
        )