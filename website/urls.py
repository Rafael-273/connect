from django.urls import path
from django.contrib.auth import views as auth_views
from django.conf import settings
from .views.music import MusicListView, MusicCreateView, MusicDeleteView, MusicUpdateView, MusicUserListView
from .views.home import HomeView, TestimonyListView, ContactView
from .views.visitor import VisitorCreateView, MemberVisitorCreateView, MemberVisitorListView
from .views.evangelism import EvangelismCreateView, EvangelismListView
from .views.member import (
    MemberCreateView, 
    NewConvertsListView,
    MemberConsolidationListView,
    MemberConsolidationDetailView,
    MemberConsolidationReportView,
    ConsolidatorGuideView,
    ConsolidatorAssignmentsView,
    RequestConsolidationView,
)
from .views.translator import AudioRecorderView, TranscriptionDisplayView
from .views.event import EventDetailView, EventListView
from .views.member_auth import (
    MemberLoginView, MemberDashboardView, MemberLogoutView, MemberProfileView,
    MemberApproveWordView, MemberRejectWordView,
    MemberRoteiroView,
    RedirectAfterLoginView,
)
from .views.member_schedule import MemberScheduleDetailView
from .views.user_management import UserManagementView, ResetUserPasswordView, ChangePasswordView
from .views.word_of_knowledge import (
    WordOfKnowledgeListView, WordOfKnowledgeCreateView, HealingCreateView,
    ServiceWordsView, MarkWordAsHealedView
)
from .views.ministration_admin import (
    MinistrationWordsListView, MinistrationHealingsListView, MinistrationMembersListView,
    MinistrationAddMemberView, MinistrationRemoveMemberView, MinistrationToggleApproverView
)
from .views.ministry import (
    MinistryListView, MinistryCreateView, MinistryUpdateView, MinistryDeleteView,
    MinistryMembersView, MinistryAddMembersPageView, MinistryAddMemberView,
    MinistryRemoveMemberView, MinistryToggleRoleView, MinistryToggleStatusView
)
from .views.ministration_dashboard import MinistrationDashboardView
from .views.course_attendance import (
    AttendanceCourseListView,
    AttendanceCourseEditView,
    AttendanceCourseDashboardView,
    AttendanceCourseAttendanceListView,
    CourseParticipantListView,
    CourseParticipantCreateView,
    CourseParticipantDetailView,
    CourseParticipantDeleteView,
    CourseLessonListView,
    CourseLessonCreateView,
    CourseLessonDetailView,
    CourseLessonAttendanceView,
    AttendanceCourseReportExportView,
    AttendanceCourseReportPrintView,
    AttendanceLessonReportPrintView,
)
from .views.word_approval import PendingWordsListView, ApproveWordView, RejectWordView
from .views.schedules import (
    TeamListView, TeamCreateView, TeamEditView, TeamDeleteView,
    ScheduleListView, ScheduleCreateView, ScheduleEditView, ScheduleDetailView,
    ScheduleDeleteView,
    ScheduleDayCreateView, ScheduleDayEditView, ScheduleDayDeleteView,
    ScheduleDayGetView, ScheduleDayToggleCancelView,
    schedule_print_view,
    check_schedule_conflict_view,
    DivisionListView, DivisionCreateView, DivisionEditView, DivisionDeleteView,
    ScheduleDayDivisionAssignView
)
from .views.prayer_request import PrayerRequestCreateView, PrayerRequestListView
from .views.house_of_peace import (
    HouseOfPeacePublicCreateView,
    HouseOfPeaceAvailableListView,
    HouseOfPeaceAcceptView,
    HouseOfPeaceMyListView,
    HouseOfPeaceScheduleView,
    HouseOfPeaceCompleteView,
    HouseOfPeaceDetailView,
    HouseOfPeaceRescheduleView,
    HouseOfPeaceCancelScheduleView,
)
from .views.admin_panel import (
    DashboardView, MembersListView, VisitorsListView,
    EventsListView, NeighborhoodsListView,
    ApiDeleteItemView, MinistryCreateEditApiView, NeighborhoodCreateEditApiView,
    MemberDetailApiView, VisitorDetailApiView, MemberEditView, VisitorEditView, EventEditView,
    NeighborhoodEditView, FollowUpListView, FollowUpEditView,
    FollowUpDeleteView, FollowUpReportView, FollowUpDetailView,
    CanteenListView, CanteenEditView, CanteenDetailView, CanteenDeleteView,
    CanteenTogglePaidView, CanteenApiView, TemplatesListView, ReportsView,
    TemplateCreateView, TemplateEditView, TemplateDetailView, TemplateDeleteView,
    TestimonyListView as AdminTestimonyListView, TestimonyEditView, TestimonyDeleteView, TestimonyToggleView,
    RoteiroView, AnuncioCreateView, AnuncioEditView, AnuncioDeleteView,
    AnuncioReorderView, RoteiroPrintView,
    AdminHouseOfPeaceListView, AdminHouseOfPeaceDetailView,
    AdminHouseOfPeaceStatusView, AdminHouseOfPeaceDeleteView,
)

urlpatterns = [
    path('', HomeView.as_view(), name='home'),
    path('testemunhos/', TestimonyListView.as_view(), name='testemunhos'),
    path('contato/', ContactView.as_view(), name='contato'),
    path('visitor/', VisitorCreateView.as_view(), name='visitor'),
    path('prayer-request/', PrayerRequestCreateView.as_view(), name='prayer_request_create'),
    path('prayer-request/list', PrayerRequestListView.as_view(), name='prayer_request_list'),
    path('evangelism/', EvangelismCreateView.as_view(), name='evangelism'),

    # House of Peace — public form (no login)
    path('house-of-peace/', HouseOfPeacePublicCreateView.as_view(), name='house_of_peace_public_form'),

    # House of Peace — member area
    path('house-of-peace/available/', HouseOfPeaceAvailableListView.as_view(), name='house_of_peace_available'),
    path('house-of-peace/<int:house_id>/accept/', HouseOfPeaceAcceptView.as_view(), name='house_of_peace_accept'),
    path('house-of-peace/my/', HouseOfPeaceMyListView.as_view(), name='house_of_peace_my_list'),
    path('house-of-peace/assignment/<int:assignment_id>/schedule/', HouseOfPeaceScheduleView.as_view(), name='house_of_peace_schedule'),
    path('house-of-peace/assignment/<int:assignment_id>/reschedule/', HouseOfPeaceRescheduleView.as_view(), name='house_of_peace_reschedule'),
    path('house-of-peace/assignment/<int:assignment_id>/cancel-schedule/', HouseOfPeaceCancelScheduleView.as_view(), name='house_of_peace_cancel_schedule'),
    path('house-of-peace/assignment/<int:assignment_id>/complete/', HouseOfPeaceCompleteView.as_view(), name='house_of_peace_complete'),
    path('house-of-peace/<int:house_id>/details/', HouseOfPeaceDetailView.as_view(), name='house_of_peace_detail'),

    path('evangelism/list/', EvangelismListView.as_view(), name='evangelism_list'),
    path('new_converts/list/', NewConvertsListView.as_view(), name='new_converts_list'),
    path('member/register/', MemberCreateView.as_view(), name='member_register'),
    path('translator/recorder/', AudioRecorderView.as_view(), name='audio_recorder'),
    path('translator/', TranscriptionDisplayView.as_view(), name='transcription'),
    path('event/list', EventListView.as_view(), name='event_list'),
    path('event/<slug:slug>/', EventDetailView.as_view(), name='event_detail'),
    
    path('password-reset/', auth_views.PasswordResetView.as_view(
        template_name='password_reset/request.html',
        email_template_name='password_reset/email.html',
        html_email_template_name='password_reset/email_html.html',
        subject_template_name='password_reset/email_subject.txt',
        success_url='/password-reset/sent/',
        extra_email_context={'site_domain': settings.SITE_DOMAIN},
    ), name='password_reset_request'),
    path('password-reset/sent/', auth_views.PasswordResetDoneView.as_view(
        template_name='password_reset/email_sent.html',
    ), name='password_reset_done'),
    path('password-reset/confirm/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='password_reset/confirm.html',
        success_url='/password-reset/complete/',
    ), name='password_reset_confirm'),
    path('password-reset/complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='password_reset/complete.html',
    ), name='password_reset_complete'),

    path('login/', MemberLoginView.as_view(), name='member_login'),
    path('admin-login/', MemberLoginView.as_view(), name='admin_login'),  # Redirect old admin login to unified login
    path('redirect-after-login/', RedirectAfterLoginView.as_view(), name='redirect_after_login'),
    path('dashboard/', MemberDashboardView.as_view(), name='member_dashboard'),
    path('logout/', MemberLogoutView.as_view(), name='member_logout'),
    path('admin-logout/', MemberLogoutView.as_view(), name='admin_logout'),
    path('profile/', MemberProfileView.as_view(), name='member_profile'),
    path('roteiro-culto/', MemberRoteiroView.as_view(), name='member_roteiro'),
    
    # Member Word Approval URLs
    path('words/approve/<int:word_id>/', MemberApproveWordView.as_view(), name='member_approve_word'),
    path('words/reject/<int:word_id>/', MemberRejectWordView.as_view(), name='member_reject_word'),
    
    # Member Consolidation URLs
    path('consolidation/', MemberConsolidationListView.as_view(), name='member_consolidation'),
    path('consolidation/<int:followup_id>/', MemberConsolidationDetailView.as_view(), name='member_consolidation_detail'),
    path('consolidation/<int:followup_id>/report/', MemberConsolidationReportView.as_view(), name='member_consolidation_report'),
    path('consolidation/guide/', ConsolidatorGuideView.as_view(), name='consolidator_guide'),
    path('consolidation/assignments/', ConsolidatorAssignmentsView.as_view(), name='consolidator_assignments'),
    path('consolidation/request/<int:person_id>/', RequestConsolidationView.as_view(), name='request_consolidation'),

    # Boas Vindas - member visitor registration & list
    path('register-visitor/', MemberVisitorCreateView.as_view(), name='member_visitor_create'),
    path('visitors/', MemberVisitorListView.as_view(), name='member_visitor_list'),

    # Member Schedules
    path('schedule/<int:schedule_id>/', MemberScheduleDetailView.as_view(), name='member_schedule_detail'),
    
    # Words of Knowledge URLs
    path('words/', WordOfKnowledgeListView.as_view(), name='word_of_knowledge_list'),
    path('words/create/', WordOfKnowledgeCreateView.as_view(), name='word_of_knowledge_create'),
    path('words/healing/create/', HealingCreateView.as_view(), name='healing_create'),
    path('words/service/', ServiceWordsView.as_view(), name='service_words_view'),
    path('words/<int:word_id>/mark-healed/', MarkWordAsHealedView.as_view(), name='mark_word_as_healed'),
    
    # Ministration URLs
    path('admin-panel/ministration/', MinistrationDashboardView.as_view(), name='ministration_dashboard'),
    path('admin-panel/ministration/words/', MinistrationWordsListView.as_view(), name='ministration_words_list'),
    path('admin-panel/ministration/words/pending/', PendingWordsListView.as_view(), name='pending_words_list'),
    path('admin-panel/ministration/words/approve/<int:word_id>/', ApproveWordView.as_view(), name='approve_word'),
    path('admin-panel/ministration/words/reject/<int:word_id>/', RejectWordView.as_view(), name='reject_word'),
    path('admin-panel/ministration/healings/', MinistrationHealingsListView.as_view(), name='ministration_healings_list'),
    path('admin-panel/ministration/members/', MinistrationMembersListView.as_view(), name='ministration_members_list'),
    path('admin-panel/ministration/members/<int:member_id>/add/', MinistrationAddMemberView.as_view(), name='ministration_add_member'),
    path('admin-panel/ministration/members/<int:member_id>/remove/', MinistrationRemoveMemberView.as_view(), name='ministration_remove_member'),
    path('admin-panel/ministration/members/<int:member_id>/toggle-approver/', MinistrationToggleApproverView.as_view(), name='ministration_toggle_approver'),
    
    # Schedule URLs - Sistema de Escalas
    path('admin-panel/schedules/', ScheduleListView.as_view(), name='schedule_list'),
    path('admin-panel/schedules/new/', ScheduleCreateView.as_view(), name='schedule_create'),
    path('admin-panel/schedules/<int:schedule_id>/', ScheduleDetailView.as_view(), name='schedule_detail'),
    path('admin-panel/schedules/<int:schedule_id>/edit/', ScheduleEditView.as_view(), name='schedule_edit'),
    path('admin-panel/schedules/<int:schedule_id>/delete/', ScheduleDeleteView.as_view(), name='schedule_delete'),
    # publish/unpublish removed — schedule for current month is always active
    path('admin-panel/schedules/<int:schedule_id>/print/', schedule_print_view, name='schedule_print'),
    
    # Schedule Days URLs
    path('admin-panel/schedules/<int:schedule_id>/days/new/', ScheduleDayCreateView.as_view(), name='schedule_day_create'),
    path('admin-panel/schedules/days/<int:day_id>/get/', ScheduleDayGetView.as_view(), name='schedule_day_get'),
    path('admin-panel/schedules/days/<int:day_id>/edit/', ScheduleDayEditView.as_view(), name='schedule_day_edit'),
    path('admin-panel/schedules/days/<int:day_id>/delete/', ScheduleDayDeleteView.as_view(), name='schedule_day_delete'),
    path('admin-panel/schedules/days/<int:day_id>/toggle-cancel/', ScheduleDayToggleCancelView.as_view(), name='schedule_day_toggle_cancel'),
    path('admin-panel/schedules/check-conflict/', check_schedule_conflict_view, name='schedule_check_conflict'),
    
    # Division URLs - Subdivisões de Escala (gerenciadas inline no form da escala)
    path('admin-panel/ministries/<int:ministry_id>/divisions/', DivisionListView.as_view(), name='division_list'),
    path('admin-panel/schedules/<int:schedule_id>/divisions/new/', DivisionCreateView.as_view(), name='division_create'),
    path('admin-panel/divisions/<int:division_id>/edit/', DivisionEditView.as_view(), name='division_edit'),
    path('admin-panel/divisions/<int:division_id>/delete/', DivisionDeleteView.as_view(), name='division_delete'),
    path('admin-panel/schedules/days/<int:day_id>/divisions/', ScheduleDayDivisionAssignView.as_view(), name='schedule_day_division_assign'),
    
    # Team URLs
    path('admin-panel/teams/', TeamListView.as_view(), name='team_list'),
    path('admin-panel/teams/new/', TeamCreateView.as_view(), name='team_create'),
    path('admin-panel/teams/<int:team_id>/edit/', TeamEditView.as_view(), name='team_edit'),
    path('admin-panel/teams/<int:team_id>/delete/', TeamDeleteView.as_view(), name='team_delete'),
    
    # Admin Panel URLs
    path('admin-panel/', DashboardView.as_view(), name='admin_dashboard'),
    path('admin-panel/members/', MembersListView.as_view(), name='admin_members_list'),
    path('admin-panel/members/new/', MemberEditView.as_view(), name='admin_member_create'),
    path('admin-panel/members/<int:member_id>/edit/', MemberEditView.as_view(), name='admin_member_edit'),
    path('admin-panel/visitors/', VisitorsListView.as_view(), name='admin_visitors_list'),
    path('admin-panel/visitors/new/', VisitorEditView.as_view(), name='admin_visitor_create'),
    path('admin-panel/visitors/<int:visitor_id>/edit/', VisitorEditView.as_view(), name='admin_visitor_edit'),
    path('admin-panel/events/', EventsListView.as_view(), name='admin_events_list'),
    path('admin-panel/events/new/', EventEditView.as_view(), name='admin_event_create'),
    path('admin-panel/events/<int:event_id>/edit/', EventEditView.as_view(), name='admin_event_edit'),
    path('admin-panel/music/', MusicListView.as_view(), name='admin_music_list'),
    path('admin-panel/music/new/', MusicCreateView.as_view(), name='admin_music_create'),
    path('admin-panel/music/edit/<int:pk>/', MusicUpdateView.as_view(), name='admin_music_edit'),
    path('admin-panel/music/delete/<int:pk>/', MusicDeleteView.as_view(), name='admin_music_delete'),
    path('ministerio-louvor/', MusicUserListView.as_view(), name='music_user_list'),
    path('admin-panel/neighborhoods/', NeighborhoodsListView.as_view(), name='admin_neighborhoods_list'),
    path('admin-panel/neighborhoods/new/', NeighborhoodEditView.as_view(), name='admin_neighborhood_create'),
    path('admin-panel/neighborhoods/<int:neighborhood_id>/edit/', NeighborhoodEditView.as_view(), name='admin_neighborhood_edit'),
    # User Management URLs
    path('admin-panel/users/', UserManagementView.as_view(), name='admin_user_management'),
    path('admin-panel/users/<int:user_id>/reset-password/', ResetUserPasswordView.as_view(), name='admin_reset_user_password'),
    path('admin-panel/change-password/', ChangePasswordView.as_view(), name='admin_change_password'),
    
    # Follow-up URLs
    path('admin-panel/followups/', FollowUpListView.as_view(), name='admin_followups_list'),
    path('admin-panel/followups/new/', FollowUpEditView.as_view(), name='admin_followup_create'),
    path('admin-panel/followups/<int:followup_id>/edit/', FollowUpEditView.as_view(), name='admin_followup_edit'),
    path('admin-panel/followups/<int:followup_id>/detail/', FollowUpDetailView.as_view(), name='admin_followup_detail'),
    path('admin-panel/followups/<int:followup_id>/report/', FollowUpReportView.as_view(), name='admin_followup_report'),
    path('admin-panel/followups/<int:followup_id>/delete/', FollowUpDeleteView.as_view(), name='admin_followup_delete'),
    
    # TODO: Implementar as views abaixo
    # Follow-up Templates URLs
    # path('admin-panel/followup-templates/', followup_template_list_view, name='admin_followup_template_list'),
    # path('admin-panel/followup-templates/new/', followup_template_edit_view, name='admin_followup_template_create'),
    # path('admin-panel/followup-templates/<int:template_id>/edit/', followup_template_edit_view, name='admin_followup_template_edit'),
    # path('admin-panel/followup-templates/<int:template_id>/', followup_template_detail_view, name='admin_followup_template_detail'),
    # path('admin-panel/followup-templates/<int:template_id>/delete/', followup_template_delete_view, name='admin_followup_template_delete'),
    # path('admin-panel/followup-templates/<int:template_id>/duplicate/', followup_template_duplicate_view, name='admin_followup_template_duplicate'),
    
    # Follow-up Steps URLs  
    # path('admin-panel/followups/<int:followup_id>/steps/', followup_steps_manage_view, name='admin_followup_steps_manage'),
    # path('admin-panel/followup-steps/<int:step_id>/update-status/', followup_step_update_status, name='admin_followup_step_update_status'),
    # path('admin-panel/followup-steps/<int:step_id>/update-notes/', followup_step_update_notes, name='admin_followup_step_update_notes'),
    # path('admin-panel/followups/<int:followup_id>/complete-all-steps/', followup_complete_all_steps, name='admin_followup_complete_all_steps'),
    # path('admin-panel/followups/<int:followup_id>/generate-steps/', followup_generate_steps, name='admin_followup_generate_steps'),
    
    # Follow-up Reports URLs
    # path('admin-panel/followup-reports/', followup_reports_view, name='admin_followup_reports'),
    
    # Templates e Relatórios URLs
    path('admin-panel/templates/', TemplatesListView.as_view(), name='admin_templates'),
    path('admin-panel/templates/new/', TemplateCreateView.as_view(), name='admin_template_create'),
    path('admin-panel/templates/<int:template_id>/edit/', TemplateEditView.as_view(), name='admin_template_edit'),
    path('admin-panel/templates/<int:template_id>/', TemplateDetailView.as_view(), name='admin_template_detail'),
    path('admin-panel/templates/<int:template_id>/delete/', TemplateDeleteView.as_view(), name='admin_template_delete'),
    path('admin-panel/reports/', ReportsView.as_view(), name='admin_reports'),

    # Listas de Presenca em Cursos
    path('admin-panel/attendance-courses/', AttendanceCourseListView.as_view(), name='attendance_course_list'),
    path('admin-panel/attendance-courses/new/', AttendanceCourseEditView.as_view(), name='attendance_course_create'),
    path('admin-panel/attendance-courses/<int:course_id>/edit/', AttendanceCourseEditView.as_view(), name='attendance_course_edit'),
    path('admin-panel/attendance-courses/<int:course_id>/', AttendanceCourseDashboardView.as_view(), name='attendance_course_dashboard'),
    path('admin-panel/attendance-courses/<int:course_id>/participants/', CourseParticipantListView.as_view(), name='attendance_course_participant_list'),
    path('admin-panel/attendance-courses/<int:course_id>/participants/new/', CourseParticipantCreateView.as_view(), name='attendance_course_participant_create'),
    path('admin-panel/attendance-courses/<int:course_id>/participants/add/', CourseParticipantCreateView.as_view(), name='attendance_course_participant_add'),
    path('admin-panel/attendance-courses/<int:course_id>/participants/<int:participant_id>/', CourseParticipantDetailView.as_view(), name='attendance_course_participant_detail'),
    path('admin-panel/attendance-courses/<int:course_id>/participants/<int:participant_id>/delete/', CourseParticipantDeleteView.as_view(), name='attendance_course_participant_delete'),
    path('admin-panel/attendance-courses/<int:course_id>/lessons/', CourseLessonListView.as_view(), name='attendance_course_lesson_list'),
    path('admin-panel/attendance-courses/<int:course_id>/lessons/new/', CourseLessonCreateView.as_view(), name='attendance_course_lesson_create'),
    path('admin-panel/attendance-courses/<int:course_id>/lessons/add/', CourseLessonCreateView.as_view(), name='attendance_course_lesson_add'),
    path('admin-panel/attendance-courses/<int:course_id>/lessons/<int:lesson_id>/', CourseLessonDetailView.as_view(), name='attendance_course_lesson_detail'),
    path('admin-panel/attendance-courses/<int:course_id>/attendance/', AttendanceCourseAttendanceListView.as_view(), name='attendance_course_attendance_list'),
    path('admin-panel/attendance-courses/lessons/<int:lesson_id>/attendance/', CourseLessonAttendanceView.as_view(), name='attendance_course_lesson_attendance'),
    path('admin-panel/attendance-courses/lessons/<int:lesson_id>/report/print/', AttendanceLessonReportPrintView.as_view(), name='attendance_lesson_report_print'),
    path('admin-panel/attendance-courses/<int:course_id>/report/export/', AttendanceCourseReportExportView.as_view(), name='attendance_course_report_export'),
    path('admin-panel/attendance-courses/<int:course_id>/report/print/', AttendanceCourseReportPrintView.as_view(), name='attendance_course_report_print'),
    
    # Cantina URLs — TODO: templates em admin_panel/cantina/*.html ainda não foram criados
    # path('admin-panel/cantina/', CanteenListView.as_view(), name='admin_cantina_list'),
    # path('admin-panel/cantina/new/', CanteenEditView.as_view(), name='admin_cantina_create'),
    # path('admin-panel/cantina/<int:debtor_id>/edit/', CanteenEditView.as_view(), name='admin_cantina_edit'),
    # path('admin-panel/cantina/<int:debtor_id>/', CanteenDetailView.as_view(), name='admin_cantina_detail'),
    # path('admin-panel/cantina/<int:debtor_id>/delete/', CanteenDeleteView.as_view(), name='admin_cantina_delete'),
    # path('admin-panel/cantina/<int:debtor_id>/toggle-paid/', CanteenTogglePaidView.as_view(), name='admin_cantina_toggle_paid'),
    # path('admin-panel/api/cantina/', CanteenApiView.as_view(), name='admin_cantina_api'),
    
    # Testemunhos URLs
    path('admin-panel/testimonies/', AdminTestimonyListView.as_view(), name='admin_testimonies_list'),
    path('admin-panel/testimonies/new/', TestimonyEditView.as_view(), name='admin_testimony_create'),
    path('admin-panel/testimonies/<int:testimony_id>/edit/', TestimonyEditView.as_view(), name='admin_testimony_edit'),
    path('admin-panel/testimonies/<int:testimony_id>/delete/', TestimonyDeleteView.as_view(), name='admin_testimony_delete'),
    path('admin-panel/testimonies/<int:testimony_id>/toggle/', TestimonyToggleView.as_view(), name='admin_testimony_toggle'),

    # Roteiro de Culto
    # Casa de Paz (admin)
    path('admin-panel/house-of-peace/', AdminHouseOfPeaceListView.as_view(), name='admin_house_of_peace_list'),
    path('admin-panel/house-of-peace/<int:house_id>/', AdminHouseOfPeaceDetailView.as_view(), name='admin_house_of_peace_detail'),
    path('admin-panel/house-of-peace/<int:house_id>/status/', AdminHouseOfPeaceStatusView.as_view(), name='admin_house_of_peace_status'),
    path('admin-panel/house-of-peace/<int:house_id>/delete/', AdminHouseOfPeaceDeleteView.as_view(), name='admin_house_of_peace_delete'),

    path('admin-panel/roteiro/', RoteiroView.as_view(), name='roteiro_view'),
    path('admin-panel/roteiro/anuncios/new/', AnuncioCreateView.as_view(), name='roteiro_anuncio_create'),
    path('admin-panel/roteiro/anuncios/<int:anuncio_id>/edit/', AnuncioEditView.as_view(), name='roteiro_anuncio_edit'),
    path('admin-panel/roteiro/anuncios/<int:anuncio_id>/delete/', AnuncioDeleteView.as_view(), name='roteiro_anuncio_delete'),
    path('admin-panel/roteiro/anuncios/reorder/', AnuncioReorderView.as_view(), name='roteiro_anuncio_reorder'),
    path('admin-panel/roteiro/print/', RoteiroPrintView.as_view(), name='roteiro_print'),

    # APIs
    path('admin-panel/api/delete/', ApiDeleteItemView.as_view(), name='admin_api_delete'),
    path('admin-panel/api/ministry/', MinistryCreateEditApiView.as_view(), name='admin_ministry_api'),
    path('admin-panel/api/neighborhood/', NeighborhoodCreateEditApiView.as_view(), name='admin_neighborhood_api'),
    path('admin-panel/api/member/<int:member_id>/', MemberDetailApiView.as_view(), name='admin_member_detail_api'),
    path('admin-panel/api/visitor/<int:visitor_id>/', VisitorDetailApiView.as_view(), name='admin_visitor_detail_api'),
    
    # Ministérios - Nova Gestão Escalável
    path('admin-panel/ministries/', MinistryListView.as_view(), name='ministry_list'),
    path('admin-panel/ministries/create/', MinistryCreateView.as_view(), name='ministry_create'),
    path('admin-panel/ministries/<int:pk>/edit/', MinistryUpdateView.as_view(), name='ministry_edit'),
    path('admin-panel/ministries/<int:pk>/delete/', MinistryDeleteView.as_view(), name='ministry_delete'),
    path('admin-panel/ministries/<int:pk>/members/', MinistryMembersView.as_view(), name='ministry_members'),
    path('admin-panel/ministries/<int:pk>/members/add/', MinistryAddMembersPageView.as_view(), name='ministry_add_members'),
    path('admin-panel/ministries/<int:ministry_id>/members/add/<int:member_id>/', MinistryAddMemberView.as_view(), name='ministry_add_member'),
    path('admin-panel/ministries/<int:ministry_id>/members/remove/<int:member_id>/', MinistryRemoveMemberView.as_view(), name='ministry_remove_member'),
    path('admin-panel/ministries/<int:ministry_id>/members/toggle-role/<int:member_id>/', MinistryToggleRoleView.as_view(), name='ministry_toggle_role'),
    path('admin-panel/ministries/<int:ministry_id>/members/toggle-status/<int:member_id>/', MinistryToggleStatusView.as_view(), name='ministry_toggle_status'),
]