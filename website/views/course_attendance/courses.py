from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.views import View

from ..mixins import AdminRequiredMixin
from ...forms.course_attendance import AttendanceCourseForm
from ...models.course_attendance import AttendanceCourse, CourseAttendance
from .mixin import CourseQueryMixin


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
