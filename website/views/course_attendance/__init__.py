from .courses import (
    AttendanceCourseAttendanceListView,
    AttendanceCourseDashboardView,
    AttendanceCourseEditView,
    AttendanceCourseListView,
)
from .lessons import (
    CourseLessonAttendanceView,
    CourseLessonCreateView,
    CourseLessonDetailView,
    CourseLessonListView,
)
from .participants import (
    CourseParticipantCreateView,
    CourseParticipantDeleteView,
    CourseParticipantDetailView,
    CourseParticipantListView,
)
from .reports import (
    AttendanceCourseReportExportView,
    AttendanceCourseReportPrintView,
    AttendanceLessonReportPrintView,
)

__all__ = [
    # Courses
    'AttendanceCourseListView',
    'AttendanceCourseEditView',
    'AttendanceCourseDashboardView',
    'AttendanceCourseAttendanceListView',
    # Participants
    'CourseParticipantListView',
    'CourseParticipantCreateView',
    'CourseParticipantDetailView',
    'CourseParticipantDeleteView',
    # Lessons
    'CourseLessonListView',
    'CourseLessonCreateView',
    'CourseLessonDetailView',
    'CourseLessonAttendanceView',
    # Reports
    'AttendanceCourseReportExportView',
    'AttendanceCourseReportPrintView',
    'AttendanceLessonReportPrintView',
]
