from datetime import date, timedelta

from django.shortcuts import get_object_or_404

from ...models.course_attendance import AttendanceCourse


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

        if recurrence_type in (
            AttendanceCourse.RECURRENCE_WEEKLY,
            AttendanceCourse.RECURRENCE_BIWEEKLY,
        ):
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
