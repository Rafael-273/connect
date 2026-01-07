from django.urls import path
from django.contrib.auth import views as auth_views
from .views.music import MusicListView
from .views.home import HomeView
from .views.visitor import VisitorCreateView, VisitorListView
from .views.member import (
    MemberCreateView, 
    NewConvertsListView,
    member_consolidation_list,
    member_consolidation_detail,
    member_consolidation_report,
    consolidator_guide,
    consolidator_assignments,
    request_consolidation
)
from .views.translator import AudioRecorderView, TranscriptionDisplayView
from .views.event import EventDetailView, EventListView
from .views.member_auth import MemberLoginView, MemberDashboardView, member_logout_view, member_profile_view, member_consolidation_view, member_approve_word, member_reject_word, redirect_after_login
from .views.user_management import user_management_view, reset_user_password, change_password_view
from .views.word_of_knowledge import (
    word_of_knowledge_list, word_of_knowledge_create, healing_create,
    service_words_view, mark_word_as_healed
)
from .views.ministration_admin import (
    MinistrationWordsListView, MinistrationHealingsListView, MinistrationMembersListView,
    ministration_add_member, ministration_remove_member, ministration_toggle_approver
)
from .views.ministry import (
    MinistryListView, MinistryCreateView, MinistryUpdateView, MinistryDeleteView,
    MinistryMembersView, ministry_add_member, ministry_remove_member,
    ministry_toggle_role, ministry_toggle_status
)
from .views.ministration_dashboard import ministration_dashboard
from .views.word_approval import pending_words_list, approve_word, reject_word
from .views.schedules import (
    team_list_view, team_create_view, team_edit_view, team_delete_view,
    schedule_list_view, schedule_create_view, schedule_edit_view, schedule_detail_view,
    schedule_delete_view, schedule_export_pdf_view,
    schedule_day_create_view, schedule_day_edit_view, schedule_day_delete_view,
    schedule_day_toggle_cancel_view
)
from .views.admin_panel import (
    dashboard_view, members_list_view, visitors_list_view,
    events_list_view, ministries_list_view, neighborhoods_list_view,
    api_delete_item, ministry_create_edit_api, neighborhood_create_edit_api,
    member_detail_api, visitor_detail_api, member_edit_view, visitor_edit_view, event_edit_view,
    ministry_edit_view, neighborhood_edit_view, followup_list_view, followup_edit_view,
    followup_delete_view, followup_report_view, followup_detail_view, profile_view,
    canteen_list_view, canteen_edit_view, canteen_detail_view, canteen_delete_view,
    canteen_toggle_paid, canteen_api, templates_view, reports_view,
    template_create_view, template_edit_view, template_detail_view, template_delete_view
)

urlpatterns = [
    path('', HomeView.as_view(), name='home'),
    path('visitor/', VisitorCreateView.as_view(), name='visitor'),
    path('visitor/list/', VisitorListView.as_view(), name='visitor_list'),
    path('new_converts/list/', NewConvertsListView.as_view(), name='new_converts_list'),
    path('member/register/', MemberCreateView.as_view(), name='member_register'),
    path('translator/recorder/', AudioRecorderView.as_view(), name='audio_recorder'),
    path('translator/', TranscriptionDisplayView.as_view(), name='transcription'),
    path('event/list', EventListView.as_view(), name='event_list'),
    path('event/<slug:slug>/', EventDetailView.as_view(), name='event_detail'),
    
    # Authentication URLs (Unified Login System)
    path('login/', MemberLoginView.as_view(), name='member_login'),
    path('admin-login/', MemberLoginView.as_view(), name='admin_login'),  # Redirect old admin login to unified login
    path('redirect-after-login/', redirect_after_login, name='redirect_after_login'),  # Smart redirect
    path('dashboard/', MemberDashboardView.as_view(), name='member_dashboard'),
    path('logout/', member_logout_view, name='member_logout'),
    path('admin-logout/', member_logout_view, name='admin_logout'),  # Redirect old admin logout to unified logout
    path('profile/', member_profile_view, name='member_profile'),
    
    # Member Word Approval URLs
    path('words/approve/<int:word_id>/', member_approve_word, name='member_approve_word'),
    path('words/reject/<int:word_id>/', member_reject_word, name='member_reject_word'),
    
    # Member Consolidation URLs
    path('consolidation/', member_consolidation_list, name='member_consolidation'),
    path('consolidation/<int:followup_id>/', member_consolidation_detail, name='member_consolidation_detail'),
    path('consolidation/<int:followup_id>/report/', member_consolidation_report, name='member_consolidation_report'),
    path('consolidation/guide/', consolidator_guide, name='consolidator_guide'),
    path('consolidation/assignments/', consolidator_assignments, name='consolidator_assignments'),
    path('consolidation/request/<int:person_id>/', request_consolidation, name='request_consolidation'),
    
    # Words of Knowledge URLs
    path('words/', word_of_knowledge_list, name='word_of_knowledge_list'),
    path('words/create/', word_of_knowledge_create, name='word_of_knowledge_create'),
    path('words/healing/create/', healing_create, name='healing_create'),
    path('words/service/', service_words_view, name='service_words_view'),
    path('words/<int:word_id>/mark-healed/', mark_word_as_healed, name='mark_word_as_healed'),
    
    # Ministration URLs
    path('admin-panel/ministration/', ministration_dashboard, name='ministration_dashboard'),
    path('admin-panel/ministration/words/', MinistrationWordsListView.as_view(), name='ministration_words_list'),
    path('admin-panel/ministration/words/pending/', pending_words_list, name='pending_words_list'),
    path('admin-panel/ministration/words/approve/<int:word_id>/', approve_word, name='approve_word'),
    path('admin-panel/ministration/words/reject/<int:word_id>/', reject_word, name='reject_word'),
    path('admin-panel/ministration/healings/', MinistrationHealingsListView.as_view(), name='ministration_healings_list'),
    path('admin-panel/ministration/members/', MinistrationMembersListView.as_view(), name='ministration_members_list'),
    path('admin-panel/ministration/members/<int:member_id>/add/', ministration_add_member, name='ministration_add_member'),
    path('admin-panel/ministration/members/<int:member_id>/remove/', ministration_remove_member, name='ministration_remove_member'),
    path('admin-panel/ministration/members/<int:member_id>/toggle-approver/', ministration_toggle_approver, name='ministration_toggle_approver'),
    
    # Schedule URLs - Sistema de Escalas
    path('admin-panel/schedules/', schedule_list_view, name='schedule_list'),
    path('admin-panel/schedules/new/', schedule_create_view, name='schedule_create'),
    path('admin-panel/schedules/<int:schedule_id>/', schedule_detail_view, name='schedule_detail'),
    path('admin-panel/schedules/<int:schedule_id>/edit/', schedule_edit_view, name='schedule_edit'),
    path('admin-panel/schedules/<int:schedule_id>/delete/', schedule_delete_view, name='schedule_delete'),
    # publish/unpublish removed — schedule for current month is always active
    path('admin-panel/schedules/<int:schedule_id>/export-pdf/', schedule_export_pdf_view, name='schedule_export_pdf'),
    
    # Schedule Days URLs
    path('admin-panel/schedules/<int:schedule_id>/days/new/', schedule_day_create_view, name='schedule_day_create'),
    path('admin-panel/schedules/days/<int:day_id>/edit/', schedule_day_edit_view, name='schedule_day_edit'),
    path('admin-panel/schedules/days/<int:day_id>/delete/', schedule_day_delete_view, name='schedule_day_delete'),
    path('admin-panel/schedules/days/<int:day_id>/toggle-cancel/', schedule_day_toggle_cancel_view, name='schedule_day_toggle_cancel'),
    
    # Team URLs
    path('admin-panel/teams/', team_list_view, name='team_list'),
    path('admin-panel/teams/new/', team_create_view, name='team_create'),
    path('admin-panel/teams/<int:team_id>/edit/', team_edit_view, name='team_edit'),
    path('admin-panel/teams/<int:team_id>/delete/', team_delete_view, name='team_delete'),
    
    # Admin Panel URLs
    path('admin-panel/', dashboard_view, name='admin_dashboard'),
    path('admin-panel/members/', members_list_view, name='admin_members_list'),
    path('admin-panel/members/new/', member_edit_view, name='admin_member_create'),
    path('admin-panel/members/<int:member_id>/edit/', member_edit_view, name='admin_member_edit'),
    path('admin-panel/visitors/', visitors_list_view, name='admin_visitors_list'),
    path('admin-panel/visitors/new/', visitor_edit_view, name='admin_visitor_create'),
    path('admin-panel/visitors/<int:visitor_id>/edit/', visitor_edit_view, name='admin_visitor_edit'),
    path('admin-panel/events/', events_list_view, name='admin_events_list'),
    path('admin-panel/events/new/', event_edit_view, name='admin_event_create'),
    path('admin-panel/events/<int:event_id>/edit/', event_edit_view, name='admin_event_edit'),
    path('admin-panel/music/', MusicListView.as_view(), name='admin_music_list'),
    path('admin-panel/ministries/', ministries_list_view, name='admin_ministries_list'),
    path('admin-panel/ministries/new/', ministry_edit_view, name='admin_ministry_create'),
    path('admin-panel/ministries/<int:ministry_id>/edit/', ministry_edit_view, name='admin_ministry_edit'),
    path('admin-panel/neighborhoods/', neighborhoods_list_view, name='admin_neighborhoods_list'),
    path('admin-panel/neighborhoods/new/', neighborhood_edit_view, name='admin_neighborhood_create'),
    path('admin-panel/neighborhoods/<int:neighborhood_id>/edit/', neighborhood_edit_view, name='admin_neighborhood_edit'),
    path('admin-panel/profile/', profile_view, name='admin_profile'),
    
    # User Management URLs
    path('admin-panel/users/', user_management_view, name='admin_user_management'),
    path('admin-panel/users/<int:user_id>/reset-password/', reset_user_password, name='admin_reset_user_password'),
    path('admin-panel/change-password/', change_password_view, name='admin_change_password'),
    
    # Follow-up URLs
    path('admin-panel/followups/', followup_list_view, name='admin_followups_list'),
    path('admin-panel/followups/new/', followup_edit_view, name='admin_followup_create'),
    path('admin-panel/followups/<int:followup_id>/edit/', followup_edit_view, name='admin_followup_edit'),
    path('admin-panel/followups/<int:followup_id>/detail/', followup_detail_view, name='admin_followup_detail'),
    path('admin-panel/followups/<int:followup_id>/report/', followup_report_view, name='admin_followup_report'),
    path('admin-panel/followups/<int:followup_id>/delete/', followup_delete_view, name='admin_followup_delete'),
    
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
    path('admin-panel/templates/', templates_view, name='admin_templates'),
    path('admin-panel/templates/new/', template_create_view, name='admin_template_create'),
    path('admin-panel/templates/<int:template_id>/edit/', template_edit_view, name='admin_template_edit'),
    path('admin-panel/templates/<int:template_id>/', template_detail_view, name='admin_template_detail'),
    path('admin-panel/templates/<int:template_id>/delete/', template_delete_view, name='admin_template_delete'),
    path('admin-panel/reports/', reports_view, name='admin_reports'),
    
    # Cantina URLs
    path('admin-panel/cantina/', canteen_list_view, name='admin_cantina_list'),
    path('admin-panel/cantina/new/', canteen_edit_view, name='admin_cantina_create'),
    path('admin-panel/cantina/<int:debtor_id>/edit/', canteen_edit_view, name='admin_cantina_edit'),
    path('admin-panel/cantina/<int:debtor_id>/', canteen_detail_view, name='admin_cantina_detail'),
    path('admin-panel/cantina/<int:debtor_id>/delete/', canteen_delete_view, name='admin_cantina_delete'),
    path('admin-panel/cantina/<int:debtor_id>/toggle-paid/', canteen_toggle_paid, name='admin_cantina_toggle_paid'),
    path('admin-panel/api/cantina/', canteen_api, name='admin_cantina_api'),
    
    # APIs
    path('admin-panel/api/delete/', api_delete_item, name='admin_api_delete'),
    path('admin-panel/api/ministry/', ministry_create_edit_api, name='admin_ministry_api'),
    path('admin-panel/api/neighborhood/', neighborhood_create_edit_api, name='admin_neighborhood_api'),
    path('admin-panel/api/member/<int:member_id>/', member_detail_api, name='admin_member_detail_api'),
    path('admin-panel/api/visitor/<int:visitor_id>/', visitor_detail_api, name='admin_visitor_detail_api'),
    
    # Ministérios - Nova Gestão Escalável
    path('admin-panel/ministries/', MinistryListView.as_view(), name='ministry_list'),
    path('admin-panel/ministries/create/', MinistryCreateView.as_view(), name='ministry_create'),
    path('admin-panel/ministries/<int:pk>/edit/', MinistryUpdateView.as_view(), name='ministry_edit'),
    path('admin-panel/ministries/<int:pk>/delete/', MinistryDeleteView.as_view(), name='ministry_delete'),
    path('admin-panel/ministries/<int:pk>/members/', MinistryMembersView.as_view(), name='ministry_members'),
    path('admin-panel/ministries/<int:ministry_id>/members/add/<int:member_id>/', ministry_add_member, name='ministry_add_member'),
    path('admin-panel/ministries/<int:ministry_id>/members/remove/<int:member_id>/', ministry_remove_member, name='ministry_remove_member'),
    path('admin-panel/ministries/<int:ministry_id>/members/toggle-role/<int:member_id>/', ministry_toggle_role, name='ministry_toggle_role'),
    path('admin-panel/ministries/<int:ministry_id>/members/toggle-status/<int:member_id>/', ministry_toggle_status, name='ministry_toggle_status'),
]