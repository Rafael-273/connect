from datetime import date

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from ..mixins import AdminRequiredMixin
from ...forms.course_attendance import CourseLessonForm
from ...models.course_attendance import CourseAttendance, CourseLesson
from .mixin import CourseQueryMixin


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
            title = (
                request.POST.get('title', '').strip()
                or f'Aula {lesson_date.strftime("%d/%m/%Y")}'
            )
            was_held = True
            success_message = 'Aula confirmada com sucesso!'
        else:
            title = (
                request.POST.get('title', '').strip()
                or f'Sem aula em {lesson_date.strftime("%d/%m/%Y")}'
            )
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
        records = lesson.attendance_records.select_related('participant').order_by(
            'participant__full_name',
        )
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
        records = CourseAttendance.objects.filter(lesson=lesson)
        return {record.participant_id: record for record in records}

    def get(self, request, lesson_id):
        lesson = self._load_lesson(lesson_id)
        if not lesson.was_held:
            messages.warning(
                request,
                'Esta data foi marcada como sem aula e nao possui presenca para lancar.',
            )
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
            messages.warning(
                request,
                'Nao e possivel lancar presenca em uma data marcada como sem aula.',
            )
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
