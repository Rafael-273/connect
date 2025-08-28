from django.urls import path
from .views.home import HomeView
from .views.visitor import VisitorCreateView, VisitorListView
from .views.member import MemberCreateView, NewConvertsListView
from .views.translator import AudioRecorderView, TranscriptionDisplayView
from .views.event import EventDetailView, EventListView
from .views.auth import admin_login_view, admin_logout_view
from .views.admin_panel import (
    dashboard_view, members_list_view, visitors_list_view,
    events_list_view, ministries_list_view, neighborhoods_list_view,
    api_delete_item, ministry_create_edit_api, neighborhood_create_edit_api,
    member_detail_api, visitor_detail_api, member_edit_view, visitor_edit_view, event_edit_view,
    ministry_edit_view, neighborhood_edit_view, followup_list_view, followup_edit_view,
    followup_delete_view, followup_report_view, followup_detail_view, profile_view
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
    
    # Authentication URLs
    path('admin-login/', admin_login_view, name='admin_login'),
    path('admin-logout/', admin_logout_view, name='admin_logout'),
    
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
    path('admin-panel/ministries/', ministries_list_view, name='admin_ministries_list'),
    path('admin-panel/ministries/new/', ministry_edit_view, name='admin_ministry_create'),
    path('admin-panel/ministries/<int:ministry_id>/edit/', ministry_edit_view, name='admin_ministry_edit'),
    path('admin-panel/neighborhoods/', neighborhoods_list_view, name='admin_neighborhoods_list'),
    path('admin-panel/neighborhoods/new/', neighborhood_edit_view, name='admin_neighborhood_create'),
    path('admin-panel/neighborhoods/<int:neighborhood_id>/edit/', neighborhood_edit_view, name='admin_neighborhood_edit'),
    path('admin-panel/profile/', profile_view, name='admin_profile'),
    
    # Follow-up URLs
    path('admin-panel/followups/', followup_list_view, name='admin_followups_list'),
    path('admin-panel/followups/new/', followup_edit_view, name='admin_followup_create'),
    path('admin-panel/followups/<int:followup_id>/edit/', followup_edit_view, name='admin_followup_edit'),
    path('admin-panel/followups/<int:followup_id>/detail/', followup_detail_view, name='admin_followup_detail'),
    path('admin-panel/followups/<int:followup_id>/report/', followup_report_view, name='admin_followup_report'),
    path('admin-panel/followups/<int:followup_id>/delete/', followup_delete_view, name='admin_followup_delete'),
    
    # APIs
    path('admin-panel/api/delete/', api_delete_item, name='admin_api_delete'),
    path('admin-panel/api/ministry/', ministry_create_edit_api, name='admin_ministry_api'),
    path('admin-panel/api/neighborhood/', neighborhood_create_edit_api, name='admin_neighborhood_api'),
    path('admin-panel/api/member/<int:member_id>/', member_detail_api, name='admin_member_detail_api'),
    path('admin-panel/api/visitor/<int:visitor_id>/', visitor_detail_api, name='admin_visitor_detail_api'),
]