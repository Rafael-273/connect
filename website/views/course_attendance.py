import csv
from datetime import date, datetime, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from .mixins import AdminRequiredMixin
from ..forms.course_attendance import (
    AttendanceCourseForm,
    CourseLessonForm,
    CourseParticipantForm,
)
from ..models.member import Member
from ..models.course_attendance import (
    AttendanceCourse,
    CourseAttendance,
    CourseLesson,
    CourseParticipant,
)


class CourseQueryMixin:
    WEEKDAY_TO_INT = {
        AttendanceCourse.WEEKDAY_MONDAY: 0,
        AttendanceCourse.WEEKDAY_TUESDAY: 1,
        AttendanceCourse.WEEKDAY_WEDNESDAY: 2,
        AttendanceCourse.WEEKDAY_THURSDAY: 3,
        AttendanceCourse.WEEKDAY_FRIDAY: 4,
        AttendanceCourse.WEEKDAY_SATURDAY: 5,
        AttendanceCourse.WEEKDAY_SUNDAY: 6,
    }

    def get_course(self, course_id):
        return get_object_or_404(AttendanceCourse, id=course_id)

    def base_course_qs(self):
        return AttendanceCourse.objects.all()

    def _suggested_lessons_until(self, course):
        if course.end_date:
            return min(course.end_date, date.today())
        return date.today()

    def _existing_lesson_dates(self, course):
        return set(course.lessons.values_list('lesson_date', flat=True))

    def build_suggested_lesson_dates(self, course, limit=80):
        until = self._suggested_lessons_until(course)
        if course.start_date > until:
            return []

        recurrence_type = course.recurrence_type
        existing_dates = self._existing_lesson_dates(course)
        suggestions = []

        if recurrence_type == AttendanceCourse.RECURRENCE_WEEKLY:
            step_days = 7
        elif recurrence_type == AttendanceCourse.RECURRENCE_BIWEEKLY:
            step_days = 14
        elif recurrence_type == AttendanceCourse.RECURRENCE_CUSTOM_DAYS:
            step_days = course.recurrence_interval_days or 0
        else:
            step_days = 0

        if recurrence_type in (AttendanceCourse.RECURRENCE_WEEKLY, AttendanceCourse.RECURRENCE_BIWEEKLY):
            target_weekday = self.WEEKDAY_TO_INT.get(course.recurrence_weekday)
            if target_weekday is None:
                return []
            current = course.start_date
            delta = (target_weekday - current.weekday()) % 7
            current = current + timedelta(days=delta)
        elif recurrence_type == AttendanceCourse.RECURRENCE_CUSTOM_DAYS:
            if not step_days or step_days < 2:
                return []
            current = course.start_date
        else:
            return []

        while current <= until and len(suggestions) < limit:
            if current not in existing_dates:
                suggestions.append(current)
            current = current + timedelta(days=step_days)

        return suggestions


class AttendanceCourseListView(LoginRequiredMixin, AdminRequiredMixin, CourseQueryMixin, View):
    template_name = 'admin_panel/attendance_courses/list.html'

    def _apply_filters(self, qs, search, status):
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(description__icontains=search))
        if status == 'active':
            qs = qs.filter(is_active=True)
        elif status == 'inactive':
            qs = qs.filter(is_active=False)
        return qs

    def _build_context(self, request):
        search = request.GET.get('search', '').strip()
        status = request.GET.get('status', '').strip()

        qs = self.base_course_qs().annotate(
            participants_count=Count('participants', distinct=True),
            lessons_count=Count('lessons', filter=Q(lessons__was_held=True), distinct=True),
        ).order_by('-start_date', 'name')
        qs = self._apply_filters(qs, search, status)

        page_obj = Paginator(qs, 20).get_page(request.GET.get('page'))
        return {
            'courses': page_obj,
            'search': search,
            'status': status,
        }

    def get(self, request):
        return render(request, self.template_name, self._build_context(request))


class AttendanceCourseEditView(LoginRequiredMixin, AdminRequiredMixin, CourseQueryMixin, View):
    template_name = 'admin_panel/attendance_courses/form.html'

    def get(self, request, course_id=None):
        course = self.get_course(course_id) if course_id else None
        form = AttendanceCourseForm(instance=course)
        return render(request, self.template_name, {
            'form': form,
            'course': course,
            'title': 'Editar Curso' if course else 'Novo Curso',
        })

    def post(self, request, course_id=None):
        course = self.get_course(course_id) if course_id else None
        form = AttendanceCourseForm(request.POST, instance=course)

        if form.is_valid():
            saved = form.save()
            messages.success(request, 'Curso salvo com sucesso!')
            return redirect('attendance_course_dashboard', course_id=saved.id)

        messages.error(request, 'Verifique os campos do formulario.')
        return render(request, self.template_name, {
            'form': form,
            'course': course,
            'title': 'Editar Curso' if course else 'Novo Curso',
        })


class AttendanceCourseDashboardView(LoginRequiredMixin, AdminRequiredMixin, CourseQueryMixin, View):
    template_name = 'admin_panel/attendance_courses/dashboard.html'

    def _attendance_qs(self, course):
        return CourseAttendance.objects.select_related(
            'lesson', 'participant',
        ).filter(
            lesson__course=course,
            lesson__was_held=True,
        )

    def _stats(self, course):
        participants_count = course.participants.count()
        lessons_count = course.lessons.filter(was_held=True).count()
        attendance_qs = self._attendance_qs(course)

        present_count = attendance_qs.filter(status=CourseAttendance.STATUS_PRESENT).count()

        total_expected = participants_count * lessons_count
        attendance_rate = round((present_count / total_expected) * 100, 1) if total_expected else 0

        return {
            'participants_count': participants_count,
            'lessons_count': lessons_count,
            'attendance_rate': attendance_rate,
        }

    def get(self, request, course_id):
        course = self.get_course(course_id)
        suggested_lesson_dates = self.build_suggested_lesson_dates(course)

        context = {
            'course': course,
            'suggested_lessons_count': len(suggested_lesson_dates),
            **self._stats(course),
        }
        return render(request, self.template_name, context)


class AttendanceCourseAttendanceListView(LoginRequiredMixin, AdminRequiredMixin, CourseQueryMixin, View):
    template_name = 'admin_panel/attendance_courses/attendance_list.html'

    def _attendance_qs(self, course):
        return CourseAttendance.objects.select_related(
            'lesson', 'participant',
        ).filter(
            lesson__course=course,
            lesson__was_held=True,
        )

    def _apply_filters(self, qs, request):
        status = request.GET.get('status', '').strip()
        participant = request.GET.get('participant', '').strip()
        start_date = request.GET.get('start_date', '').strip()
        end_date = request.GET.get('end_date', '').strip()

        if status:
            qs = qs.filter(status=status)
        if participant:
            qs = qs.filter(participant__full_name__icontains=participant)
        if start_date:
            qs = qs.filter(lesson__lesson_date__gte=start_date)
        if end_date:
            qs = qs.filter(lesson__lesson_date__lte=end_date)

        return qs

    def get(self, request, course_id):
        course = self.get_course(course_id)
        attendance_rows = self._apply_filters(self._attendance_qs(course), request).order_by(
            '-lesson__lesson_date', 'participant__full_name',
        )
        report_page = Paginator(attendance_rows, 30).get_page(request.GET.get('page'))

        lessons_search = request.GET.get('search', '').strip()
        lessons_start_date = request.GET.get('lesson_start_date', '').strip()
        lessons_end_date = request.GET.get('lesson_end_date', '').strip()
        lessons_progress = request.GET.get('lesson_progress', '').strip()

        lessons = course.lessons.filter(was_held=True).annotate(
            records_count=Count('attendance_records', distinct=True),
        ).order_by('-lesson_date')

        if lessons_search:
            lessons = lessons.filter(title__icontains=lessons_search)
        if lessons_start_date:
            lessons = lessons.filter(lesson_date__gte=lessons_start_date)
        if lessons_end_date:
            lessons = lessons.filter(lesson_date__lte=lessons_end_date)

        participants_count = course.participants.count()
        if lessons_progress == 'with_records':
            lessons = lessons.filter(records_count__gt=0)
        elif lessons_progress == 'without_records':
            lessons = lessons.filter(records_count=0)
        elif lessons_progress == 'complete' and participants_count > 0:
            lessons = lessons.filter(records_count__gte=participants_count)
        elif lessons_progress == 'incomplete' and participants_count > 0:
            lessons = lessons.filter(records_count__lt=participants_count)

        return render(request, self.template_name, {
            'course': course,
            'report_page': report_page,
            'lessons': lessons,
            'participants_count': participants_count,
            'lessons_search': lessons_search,
            'lessons_start_date': lessons_start_date,
            'lessons_end_date': lessons_end_date,
            'lessons_progress': lessons_progress,
            'report_status': request.GET.get('status', ''),
            'report_participant': request.GET.get('participant', ''),
            'report_start_date': request.GET.get('start_date', ''),
            'report_end_date': request.GET.get('end_date', ''),
        })


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
                messages.success(request, f'{created_count} membro(s) adicionado(s). {skipped} ja estava(m) cadastrado(s).')
            elif created_count:
                messages.success(request, f'{created_count} membro(s) adicionado(s) com sucesso!')
            else:
                messages.warning(request, 'Nenhum membro novo foi adicionado. Todos ja estavam cadastrados.')

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
        records = participant.attendance_records.select_related('lesson').order_by('-lesson__lesson_date')
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


class CourseLessonListView(LoginRequiredMixin, AdminRequiredMixin, CourseQueryMixin, View):
    template_name = 'admin_panel/attendance_courses/lesson_list.html'

    def get(self, request, course_id):
        course = self.get_course(course_id)
        lessons = course.lessons.order_by('-lesson_date')
        suggested_lesson_dates = self.build_suggested_lesson_dates(course)
        return render(request, self.template_name, {
            'course': course,
            'lessons': lessons,
            'suggested_lesson_dates': suggested_lesson_dates,
        })


class CourseLessonCreateView(LoginRequiredMixin, AdminRequiredMixin, CourseQueryMixin, View):
    template_name = 'admin_panel/attendance_courses/lesson_create.html'

    def _create_from_recurrence_action(self, request, course, action):
        lesson_date_raw = request.POST.get('lesson_date', '').strip()
        try:
            lesson_date = date.fromisoformat(lesson_date_raw)
        except ValueError:
            messages.error(request, 'Data da aula invalida para confirmacao de recorrencia.')
            return redirect('attendance_course_lesson_list', course_id=course.id)

        if course.lessons.filter(lesson_date=lesson_date).exists():
            messages.warning(request, 'Ja existe um registro para essa data.')
            return redirect('attendance_course_lesson_list', course_id=course.id)

        if action == 'confirm_held':
            title = request.POST.get('title', '').strip() or f'Aula {lesson_date.strftime("%d/%m/%Y")}'
            was_held = True
            success_message = 'Aula confirmada com sucesso!'
        else:
            title = request.POST.get('title', '').strip() or f'Sem aula em {lesson_date.strftime("%d/%m/%Y")}'
            was_held = False
            success_message = 'Data registrada como sem aula.'

        CourseLesson.objects.create(
            course=course,
            title=title,
            lesson_date=lesson_date,
            notes=request.POST.get('notes', '').strip() or None,
            was_held=was_held,
            based_on_recurrence=True,
        )
        messages.success(request, success_message)
        return redirect('attendance_course_lesson_list', course_id=course.id)

    def get(self, request, course_id):
        course = self.get_course(course_id)
        form = CourseLessonForm()
        return render(request, self.template_name, {
            'course': course,
            'form': form,
        })

    def post(self, request, course_id):
        course = self.get_course(course_id)

        quick_action = request.POST.get('quick_action', '').strip()
        if quick_action in ('confirm_held', 'mark_not_held'):
            return self._create_from_recurrence_action(request, course, quick_action)

        form = CourseLessonForm(request.POST)

        if form.is_valid():
            lesson = form.save(commit=False)
            lesson.course = course
            lesson.save()
            messages.success(request, 'Aula cadastrada com sucesso!')
            return redirect('attendance_course_lesson_list', course_id=course.id)

        messages.error(request, 'Verifique os campos do formulario de aula.')
        return render(request, self.template_name, {
            'course': course,
            'form': form,
        })


class CourseLessonDetailView(LoginRequiredMixin, AdminRequiredMixin, CourseQueryMixin, View):
    template_name = 'admin_panel/attendance_courses/lesson_detail.html'

    def get(self, request, course_id, lesson_id):
        course = self.get_course(course_id)
        lesson = get_object_or_404(CourseLesson, id=lesson_id, course=course)
        records = lesson.attendance_records.select_related('participant').order_by('participant__full_name')
        total_records = records.count()
        present_count = records.filter(status=CourseAttendance.STATUS_PRESENT).count()
        absent_count = records.filter(status=CourseAttendance.STATUS_ABSENT).count()
        excused_count = records.filter(status=CourseAttendance.STATUS_EXCUSED).count()
        return render(request, self.template_name, {
            'course': course,
            'lesson': lesson,
            'records': records,
            'total_records': total_records,
            'present_count': present_count,
            'absent_count': absent_count,
            'excused_count': excused_count,
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


class CourseLessonAttendanceView(LoginRequiredMixin, AdminRequiredMixin, View):
    template_name = 'admin_panel/attendance_courses/lesson_attendance.html'

    def _load_lesson(self, lesson_id):
        return get_object_or_404(
            CourseLesson.objects.select_related('course'),
            id=lesson_id,
        )

    def _participants(self, lesson):
        return lesson.course.participants.order_by('full_name')

    def _attendance_map(self, lesson):
        records = CourseAttendance.objects.filter(
            lesson=lesson,
        )
        return {record.participant_id: record for record in records}

    def get(self, request, lesson_id):
        lesson = self._load_lesson(lesson_id)
        if not lesson.was_held:
            messages.warning(request, 'Esta data foi marcada como sem aula e nao possui presenca para lancar.')
            return redirect('attendance_course_dashboard', course_id=lesson.course.id)

        participants = self._participants(lesson)
        attendance_map = self._attendance_map(lesson)
        participant_rows = [
            {
                'participant': participant,
                'record': attendance_map.get(participant.id),
            }
            for participant in participants
        ]

        return render(request, self.template_name, {
            'lesson': lesson,
            'participant_rows': participant_rows,
            'status_choices': CourseAttendance.STATUS_CHOICES,
        })

    def post(self, request, lesson_id):
        lesson = self._load_lesson(lesson_id)
        if not lesson.was_held:
            messages.warning(request, 'Nao e possivel lancar presenca em uma data marcada como sem aula.')
            return redirect('attendance_course_dashboard', course_id=lesson.course.id)

        participants = self._participants(lesson)

        for participant in participants:
            status = request.POST.get(f'status_{participant.id}', '').strip()
            notes = request.POST.get(f'notes_{participant.id}', '').strip()

            if not status:
                CourseAttendance.objects.filter(
                    lesson=lesson,
                    participant=participant,
                ).delete()
                continue

            CourseAttendance.objects.update_or_create(
                lesson=lesson,
                participant=participant,
                defaults={
                    'status': status,
                    'notes': notes,
                },
            )

        messages.success(request, 'Presencas atualizadas com sucesso!')
        return redirect('attendance_course_dashboard', course_id=lesson.course.id)


class AttendanceCourseReportExportView(LoginRequiredMixin, AdminRequiredMixin, CourseQueryMixin, View):
    def _filtered_qs(self, request, course):
        qs = CourseAttendance.objects.select_related(
            'lesson', 'participant',
        ).filter(
            lesson__course=course,
            lesson__was_held=True,
        )

        status = request.GET.get('status', '').strip()
        participant = request.GET.get('participant', '').strip()
        start_date = request.GET.get('start_date', '').strip()
        end_date = request.GET.get('end_date', '').strip()

        if status:
            qs = qs.filter(status=status)
        if participant:
            qs = qs.filter(participant__full_name__icontains=participant)
        if start_date:
            qs = qs.filter(lesson__lesson_date__gte=start_date)
        if end_date:
            qs = qs.filter(lesson__lesson_date__lte=end_date)

        return qs.order_by('lesson__lesson_date', 'participant__full_name')

    def get(self, request, course_id):
        course = self.get_course(course_id)
        records = self._filtered_qs(request, course)

        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="presenca_{course.id}.csv"'
        response.write('\ufeff')

        writer = csv.writer(response)
        writer.writerow(CourseAttendance.export_header())
        for record in records:
            writer.writerow(CourseAttendance.export_row(record))

        return response


class AttendanceCourseReportPrintView(LoginRequiredMixin, AdminRequiredMixin, CourseQueryMixin, View):
    template_name = 'admin_panel/attendance_courses/report_print.html'

    def _filtered_qs(self, request, course):
        qs = CourseAttendance.objects.select_related(
            'lesson', 'participant',
        ).filter(
            lesson__course=course,
            lesson__was_held=True,
        )

        status = request.GET.get('status', '').strip()
        participant = request.GET.get('participant', '').strip()
        start_date = request.GET.get('start_date', '').strip()
        end_date = request.GET.get('end_date', '').strip()

        if status:
            qs = qs.filter(status=status)
        if participant:
            qs = qs.filter(participant__full_name__icontains=participant)
        if start_date:
            qs = qs.filter(lesson__lesson_date__gte=start_date)
        if end_date:
            qs = qs.filter(lesson__lesson_date__lte=end_date)

        return qs.order_by('lesson__lesson_date', 'participant__full_name')

    def get(self, request, course_id):
        course = self.get_course(course_id)
        records = list(self._filtered_qs(request, course))

        status_counts = {
            'present': sum(1 for record in records if record.status == CourseAttendance.STATUS_PRESENT),
            'absent': sum(1 for record in records if record.status == CourseAttendance.STATUS_ABSENT),
            'excused': sum(1 for record in records if record.status == CourseAttendance.STATUS_EXCUSED),
        }

        context = {
            'course': course,
            'records': records,
            'records_count': len(records),
            'status_counts': status_counts,
            'filters': {
                'status': request.GET.get('status', '').strip(),
                'participant': request.GET.get('participant', '').strip(),
                'start_date': request.GET.get('start_date', '').strip(),
                'end_date': request.GET.get('end_date', '').strip(),
            },
            'generated_at': datetime.now().strftime('%d/%m/%Y às %H:%M'),
        }
        return render(request, self.template_name, context)


class AttendanceLessonReportPrintView(LoginRequiredMixin, AdminRequiredMixin, View):
    template_name = 'admin_panel/attendance_courses/report_print.html'

    def get(self, request, lesson_id):
        lesson = get_object_or_404(
            CourseLesson.objects.select_related('course'),
            id=lesson_id,
            was_held=True,
        )
        records = list(
            CourseAttendance.objects.select_related('lesson', 'participant').filter(
                lesson=lesson,
            ).order_by('participant__full_name')
        )

        status_counts = {
            'present': sum(1 for record in records if record.status == CourseAttendance.STATUS_PRESENT),
            'absent': sum(1 for record in records if record.status == CourseAttendance.STATUS_ABSENT),
            'excused': sum(1 for record in records if record.status == CourseAttendance.STATUS_EXCUSED),
        }

        context = {
            'course': lesson.course,
            'records': records,
            'records_count': len(records),
            'status_counts': status_counts,
            'filters': {
                'status': 'Todos',
                'participant': 'Todos',
                'start_date': lesson.lesson_date.strftime('%Y-%m-%d'),
                'end_date': lesson.lesson_date.strftime('%Y-%m-%d'),
            },
            'generated_at': datetime.now().strftime('%d/%m/%Y às %H:%M'),
        }
        return render(request, self.template_name, context)
