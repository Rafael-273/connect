import csv
from datetime import datetime

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views import View

from ..mixins import AdminRequiredMixin
from ...models.course_attendance import CourseAttendance, CourseLesson
from .mixin import CourseQueryMixin


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
            'present': sum(1 for r in records if r.status == CourseAttendance.STATUS_PRESENT),
            'absent': sum(1 for r in records if r.status == CourseAttendance.STATUS_ABSENT),
            'excused': sum(1 for r in records if r.status == CourseAttendance.STATUS_EXCUSED),
        }

        return render(request, self.template_name, {
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
        })


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
            'present': sum(1 for r in records if r.status == CourseAttendance.STATUS_PRESENT),
            'absent': sum(1 for r in records if r.status == CourseAttendance.STATUS_ABSENT),
            'excused': sum(1 for r in records if r.status == CourseAttendance.STATUS_EXCUSED),
        }

        return render(request, self.template_name, {
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
        })
