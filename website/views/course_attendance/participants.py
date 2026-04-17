from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from ..mixins import AdminRequiredMixin
from ...forms.course_attendance import CourseParticipantForm
from ...models.course_attendance import CourseAttendance, CourseParticipant
from ...models.member import Member
from .mixin import CourseQueryMixin


class CourseParticipantListView(LoginRequiredMixin, AdminRequiredMixin, CourseQueryMixin, View):
    template_name = 'admin_panel/attendance_courses/participant_list.html'

    def get(self, request, course_id):
        course = self.get_course(course_id)
        search = request.GET.get('search', '').strip()
        participants_qs = course.participants.order_by('full_name')
        if search:
            participants_qs = participants_qs.filter(
                Q(full_name__icontains=search)
                | Q(email__icontains=search)
                | Q(phone__icontains=search)
            )

        participants = Paginator(participants_qs, 20).get_page(request.GET.get('page'))
        return render(request, self.template_name, {
            'course': course,
            'participants': participants,
            'search': search,
        })


class CourseParticipantCreateView(LoginRequiredMixin, AdminRequiredMixin, CourseQueryMixin, View):
    template_name = 'admin_panel/attendance_courses/participant_create.html'

    def _available_members_qs(self, course):
        return Member.objects.filter(is_active=True).exclude(
            course_participations__course=course,
        ).order_by('name').distinct()

    def get(self, request, course_id):
        course = self.get_course(course_id)
        form = CourseParticipantForm()
        return render(request, self.template_name, {
            'course': course,
            'form': form,
            'bulk_members': self._available_members_qs(course),
        })

    def post(self, request, course_id):
        course = self.get_course(course_id)
        participant_type = request.POST.get('participant_type', 'non_member').strip()

        if participant_type == 'bulk_members':
            selected_ids = [v for v in request.POST.getlist('bulk_member_ids') if v]
            if not selected_ids:
                form = CourseParticipantForm()
                messages.error(request, 'Selecione pelo menos um membro para adicionar em lote.')
                return render(request, self.template_name, {
                    'course': course,
                    'form': form,
                    'bulk_members': self._available_members_qs(course),
                })

            existing_member_ids = set(
                course.participants.exclude(member__isnull=True).values_list('member_id', flat=True)
            )
            members = Member.objects.filter(
                id__in=selected_ids,
                is_active=True,
            ).order_by('name')

            to_create = []
            skipped = 0
            for member in members:
                if member.id in existing_member_ids:
                    skipped += 1
                    continue
                to_create.append(CourseParticipant(
                    course=course,
                    member=member,
                    full_name=member.name,
                    phone=member.phone,
                    email=member.email,
                ))

            if to_create:
                CourseParticipant.objects.bulk_create(to_create)

            created_count = len(to_create)
            if created_count and skipped:
                messages.success(
                    request,
                    f'{created_count} membro(s) adicionado(s). {skipped} ja estava(m) cadastrado(s).',
                )
            elif created_count:
                messages.success(request, f'{created_count} membro(s) adicionado(s) com sucesso!')
            else:
                messages.warning(
                    request, 'Nenhum membro novo foi adicionado. Todos ja estavam cadastrados.',
                )

            return redirect('attendance_course_participant_list', course_id=course.id)

        post_data = request.POST.copy()
        if participant_type == 'non_member':
            post_data['member'] = ''

        form = CourseParticipantForm(post_data)

        if participant_type == 'member' and not post_data.get('member'):
            form.add_error('member', 'Selecione um membro cadastrado.')

        if form.is_valid():
            participant = form.save(commit=False)
            participant.course = course
            participant.save()
            messages.success(request, 'Participante adicionado com sucesso!')
            return redirect('attendance_course_participant_list', course_id=course.id)

        messages.error(request, 'Verifique os campos do formulario de participante.')
        return render(request, self.template_name, {
            'course': course,
            'form': form,
            'bulk_members': self._available_members_qs(course),
        })


class CourseParticipantDetailView(LoginRequiredMixin, AdminRequiredMixin, CourseQueryMixin, View):
    template_name = 'admin_panel/attendance_courses/participant_detail.html'

    def get(self, request, course_id, participant_id):
        course = self.get_course(course_id)
        participant = get_object_or_404(CourseParticipant, id=participant_id, course=course)
        records = participant.attendance_records.select_related('lesson').order_by(
            '-lesson__lesson_date',
        )
        total_records = records.count()
        present_count = records.filter(status=CourseAttendance.STATUS_PRESENT).count()
        attendance_rate = round((present_count / total_records) * 100, 1) if total_records else 0
        return render(request, self.template_name, {
            'course': course,
            'participant': participant,
            'records': records,
            'total_records': total_records,
            'present_count': present_count,
            'attendance_rate': attendance_rate,
        })


class CourseParticipantDeleteView(LoginRequiredMixin, AdminRequiredMixin, CourseQueryMixin, View):
    def post(self, request, course_id, participant_id):
        course = self.get_course(course_id)
        participant = get_object_or_404(
            CourseParticipant,
            id=participant_id,
            course=course,
        )
        participant.delete()
        messages.success(request, 'Participante removido com sucesso!')

        next_url = request.POST.get('next', '').strip()
        if next_url == 'participant_page':
            return redirect('attendance_course_participant_list', course_id=course.id)
        return redirect('attendance_course_dashboard', course_id=course.id)
