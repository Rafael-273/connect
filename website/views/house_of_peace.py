"""
Views for House of Peace module.

Flow:
  - Public form (HouseOfPeacePublicCreateView) — anyone registers the family.
  - Member dashboard shows "House of Peace" card.
  - HouseOfPeaceAvailableListView — lists available houses for member to accept.
  - HouseOfPeaceAcceptView — member accepts a house (max 3 per house).
  - HouseOfPeaceMyListView — lists houses linked to member (active and completed).
  - HouseOfPeaceScheduleView — member registers scheduled day.
  - HouseOfPeaceCompleteView — member registers report, healing/testimony and continuity.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone

from ..models.house_of_peace import HouseOfPeace, HouseOfPeaceAssignment
from ..forms.house_of_peace import HouseOfPeacePublicForm
from .mixins import MemberRequiredMixin, MinistrationContextMixin


# ---------------------------------------------------------------------------
# Formulário Público (sem login)
# ---------------------------------------------------------------------------

class HouseOfPeacePublicCreateView(View):
    """Public form for people to register."""

    def _get_neighborhoods(self):
        from ..models.neighborhood import Neighborhood
        return Neighborhood.objects.all().order_by('name')

    def get(self, request):
        form = HouseOfPeacePublicForm()
        return render(request, 'house_of_peace/public_form.html', {
            'form': form,
            'back_url': self._get_back_url(request),
        })

    def post(self, request):
        form = HouseOfPeacePublicForm(request.POST)
        back_url = self._get_back_url(request)
        
        if form.is_valid():
            # Form already handles saving and converting prayer_types to string
            casa = form.save()
            casa.status = 'available'
            casa.save()
            
            request.session['house_of_peace_public_success'] = {
                'submitted_name': casa.family_name,
                'back_url': back_url,
            }
            return redirect('house_of_peace_public_success')
        
        # If form is invalid, show errors
        return render(request, 'house_of_peace/public_form.html', {
            'form': form,
            'back_url': back_url,
        })

    def _get_back_url(self, request):
        if request.user.is_authenticated and hasattr(request.user, 'member'):
            return 'member_dashboard'
        return 'home'


class HouseOfPeacePublicSuccessView(View):
    """Confirmation page shown after a successful public submission."""

    def get(self, request):
        success_data = request.session.get('house_of_peace_public_success')
        if not success_data:
            return redirect('house_of_peace_public_form')

        return render(request, 'house_of_peace/public_success.html', {
            'submitted_name': success_data.get('submitted_name'),
            'back_url': success_data.get('back_url', 'home'),
        })


# ---------------------------------------------------------------------------
# Área do Membro
# ---------------------------------------------------------------------------

class HouseOfPeaceAvailableListView(MemberRequiredMixin, MinistrationContextMixin, View):
    """List available Houses of Peace for member to accept."""

    def get(self, request):
        member = self.member

        # Available houses (status=available, still accept more members)
        all_available = HouseOfPeace.objects.filter(status='available').prefetch_related('assignments')
        available = [c for c in all_available if c.can_accept_more]

        # IDs of houses that the member already accepted
        my_ids = set(
            HouseOfPeaceAssignment.objects.filter(member=member)
            .values_list('house_of_peace_id', flat=True)
        )

        # Filter houses that the member hasn't accepted yet
        not_mine = [c for c in available if c.id not in my_ids]

        context = {
            'available_houses': not_mine,
            'my_houses_count': HouseOfPeaceAssignment.objects.filter(member=member).count(),
            'member': member,
            'can_consolidate': member.is_available_to_consolidate,
            'is_ministration_member': self.get_ministration_status(member),
        }
        return render(request, 'house_of_peace/available_list.html', context)


class HouseOfPeaceAcceptView(MemberRequiredMixin, View):
    """Member accepts a House of Peace (POST via AJAX or form)."""

    def post(self, request, house_id):
        member = self.member
        casa = get_object_or_404(HouseOfPeace, id=house_id, status='available')

        # Check if already accepted
        if HouseOfPeaceAssignment.objects.filter(member=member, house_of_peace=casa).exists():
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': 'You already accepted this House of Peace.'})
            messages.warning(request, 'You already accepted this House of Peace.')
            return redirect('house_of_peace_available')

        # Check limit of 3 members
        if not casa.can_accept_more:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': 'This House of Peace has already reached the limit of 3 members.'})
            messages.error(request, 'This House of Peace has already reached the limit of 3 members.')
            return redirect('house_of_peace_available')

        assignment = HouseOfPeaceAssignment.objects.create(
            house_of_peace=casa,
            member=member,
            status='accepted',
        )

        # If reached 3 members, change status to in_progress
        if casa.assignments.count() >= 3:
            casa.status = 'in_progress'
            casa.save()

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'assignment_id': assignment.id,
                'message': f'You accepted the House of Peace from {casa.family_name}!',
                'phone': casa.phone,
            })

        messages.success(request, f'You accepted the House of Peace from {casa.family_name}!')
        return redirect('house_of_peace_my_list')


class HouseOfPeaceMyListView(MemberRequiredMixin, MinistrationContextMixin, View):
    """List Houses of Peace linked to the member."""

    def get(self, request):
        member = self.member
        assignments = (
            HouseOfPeaceAssignment.objects
            .filter(member=member)
            .select_related('house_of_peace')
            .order_by('-accepted_at')
        )

        active = [a for a in assignments if a.status in ('accepted', 'scheduled')]
        completed = [a for a in assignments if a.status == 'completed']

        context = {
            'active_assignments': active,
            'completed_assignments': completed,
            'member': member,
            'can_consolidate': member.is_available_to_consolidate,
            'is_ministration_member': self.get_ministration_status(member),
        }
        return render(request, 'house_of_peace/my_list.html', context)


class HouseOfPeaceScheduleView(MemberRequiredMixin, View):
    """Member registers the scheduled day for the visit."""

    def post(self, request, assignment_id):
        assignment = get_object_or_404(
            HouseOfPeaceAssignment,
            id=assignment_id,
            member=self.member,
        )
        scheduled_date = request.POST.get('scheduled_date')
        if not scheduled_date:
            messages.error(request, 'Please inform the scheduled date.')
            return redirect('house_of_peace_my_list')

        assignment.scheduled_date = scheduled_date
        assignment.status = 'scheduled'
        assignment.save()

        messages.success(request, 'Visit date registered successfully!')
        return redirect('house_of_peace_my_list')


class HouseOfPeaceCompleteView(MemberRequiredMixin, MinistrationContextMixin, View):
    """Member registers the report after the visit."""

    def get(self, request, assignment_id):
        assignment = get_object_or_404(
            HouseOfPeaceAssignment,
            id=assignment_id,
            member=self.member,
        )
        return render(request, 'house_of_peace/complete_form.html', {
            'assignment': assignment,
            'can_consolidate': self.member.is_available_to_consolidate,
            'is_ministration_member': self.get_ministration_status(self.member),
        })

    def post(self, request, assignment_id):
        from ..models.testimony import Testimony
        from ..models.word_of_knowledge import Healing
        from datetime import date

        assignment = get_object_or_404(
            HouseOfPeaceAssignment,
            id=assignment_id,
            member=self.member,
        )

        report = request.POST.get('report', '').strip()
        had_healing = request.POST.get('had_healing') == 'on'
        healed_person_name = request.POST.get('healed_person_name', '').strip()
        body_part = request.POST.get('body_part', '').strip()
        condition = request.POST.get('condition', '').strip()
        healing_type = request.POST.get('healing_type', 'total')
        healing_desc = request.POST.get('healing_description', '').strip()
        
        had_testimony = request.POST.get('had_testimony') == 'on'
        testimony_desc = request.POST.get('testimony_description', '').strip()
        will_continue = request.POST.get('will_continue') == 'on'
        next_visit_date = request.POST.get('next_visit_date', '').strip()

        if not report:
            messages.error(request, 'Por favor, escreva um breve relato da visita.')
            return render(request, 'house_of_peace/complete_form.html', {
                'assignment': assignment,
                'can_consolidate': self.member.is_available_to_consolidate,
                'is_ministration_member': self.get_ministration_status(self.member),
            })

        assignment.complete(
            report=report,
            had_healing=had_healing,
            healing_desc=healing_desc or None,
            had_testimony=had_testimony,
            testimony_desc=testimony_desc or None,
            will_continue=will_continue,
        )

        # If will_continue, create a new assignment for the same member
        if will_continue:
            # Only create if no active assignment exists
            active_exists = HouseOfPeaceAssignment.objects.filter(
                house_of_peace=assignment.house_of_peace,
                member=self.member,
                status__in=['accepted', 'scheduled']
            ).exists()
            
            if not active_exists:
                new_assignment = HouseOfPeaceAssignment.objects.create(
                    house_of_peace=assignment.house_of_peace,
                    member=self.member,
                    status='scheduled' if next_visit_date else 'accepted',
                    scheduled_date=next_visit_date if next_visit_date else None,
                )

        # Create linked Healing record
        if had_healing and healing_desc:
            Healing.objects.create(
                member=self.member,
                description=healing_desc,
                healed_person_name=healed_person_name or None,
                body_part=body_part or None,
                condition=condition or None,
                healing_type=healing_type,
                healing_date=date.today(),
                house_of_peace_assignment=assignment,
            )

        # Create linked Testimony records
        family_name = assignment.house_of_peace.family_name
        if had_testimony and testimony_desc:
            Testimony.objects.create(
                member=self.member,
                title=f'Testemunho — Casa de Paz: {family_name}',
                category='other',
                testimony_date=date.today(),
                source='house_of_peace',
                house_of_peace_assignment=assignment,
            )

        if will_continue:
            if next_visit_date:
                messages.success(
                    request,
                    f'Relato registrado com sucesso! Casa de Paz reatribuída para {next_visit_date}.'
                )
            else:
                messages.success(
                    request,
                    'Relato registrado com sucesso! Casa de Paz reatribuída — você pode agendar a próxima visita!'
                )
        else:
            messages.success(request, 'Relato registrado com sucesso! Casa de Paz concluída.')
        return redirect('house_of_peace_my_list')


class HouseOfPeaceDetailView(MemberRequiredMixin, MinistrationContextMixin, View):
    """Details of a specific House of Peace (modal or page)."""

    def get(self, request, house_id):
        casa = get_object_or_404(HouseOfPeace, id=house_id)
        member = self.member

        my_assignment = HouseOfPeaceAssignment.objects.filter(
            member=member, house_of_peace=casa
        ).first()

        # Phone is only displayed if member already accepted the house
        show_phone = my_assignment is not None

        context = {
            'casa': casa,
            'my_assignment': my_assignment,
            'show_phone': show_phone,
            'assignments': casa.assignments.select_related('member').all(),
            'can_consolidate': member.is_available_to_consolidate,
            'is_ministration_member': self.get_ministration_status(member),
        }

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            prayer_types_display = casa.prayer_types_display
            return JsonResponse({
                'success': True,
                'family_name': casa.family_name,
                'requester_name': casa.requester_name,
                'neighborhood': casa.neighborhood.name if casa.neighborhood else '',
                'address': casa.address or '',
                'phone': casa.phone if show_phone else None,
                'family_size': casa.family_size,
                'prayer_types': prayer_types_display,
                'prayer_description': casa.prayer_description or '',
                'status': casa.get_status_display(),
                'assignments_count': casa.assignments_count,
                'can_accept_more': casa.can_accept_more,
                'show_phone': show_phone,
            })

        return render(request, 'house_of_peace/detail.html', context)


class HouseOfPeaceRescheduleView(MemberRequiredMixin, View):
    """Member reschedules a visit (changes scheduled_date)."""

    def post(self, request, assignment_id):
        assignment = get_object_or_404(
            HouseOfPeaceAssignment,
            id=assignment_id,
            member=self.member,
            status__in=['scheduled', 'accepted']
        )
        
        new_date = request.POST.get('new_scheduled_date', '').strip()
        
        if not new_date:
            messages.error(request, 'Por favor, informe uma data.')
            return redirect('house_of_peace_my_list')
        
        old_date = assignment.scheduled_date
        assignment.scheduled_date = new_date
        assignment.status = 'scheduled'
        assignment.save()
        
        if old_date:
            messages.success(
                request,
                f'Data remarcada com sucesso! De {old_date} para {new_date}.'
            )
        else:
            messages.success(
                request,
                f'Visita agendada para {new_date}!'
            )
        
        return redirect('house_of_peace_my_list')


class HouseOfPeaceCancelScheduleView(MemberRequiredMixin, View):
    """Member cancels a scheduled visit or leaves the assignment."""

    def post(self, request, assignment_id):
        assignment = get_object_or_404(
            HouseOfPeaceAssignment,
            id=assignment_id,
            member=self.member,
            status__in=['scheduled', 'accepted']
        )
        
        delete_assignment = request.POST.get('delete_assignment', '').lower() == 'true'
        
        if delete_assignment:
            # User is leaving this house assignment completely
            family_name = assignment.house_of_peace.requester_name
            assignment.delete()
            messages.success(
                request,
                f'Você saiu da visita na família {family_name}.'
            )
        else:
            # Only remove scheduled_date, keep assignment as 'accepted'
            assignment.scheduled_date = None
            assignment.status = 'accepted'
            assignment.save()
            messages.success(
                request,
                'Visita desmarcada. Você pode agendar uma nova data quando achar melhor.'
            )
        
        return redirect('house_of_peace_my_list')
