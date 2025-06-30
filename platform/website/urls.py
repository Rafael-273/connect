from django.urls import path
from .views.home import HomeView
from .views.visitor import VisitorCreateView, VisitorListView
from .views.member import MemberCreateView
from .views.translator import AudioRecorderView, TranscriptionDisplayView
from .views.event import EventDetailView, EventListView

urlpatterns = [
    path('', HomeView.as_view(), name='home'),
    path('visitor/', VisitorCreateView.as_view(), name='visitor'),
    path('visitor/list/', VisitorListView.as_view(), name='visitor_list'),
    path('member/register/', MemberCreateView.as_view(), name='member_register'),
    path('translator/recorder/', AudioRecorderView.as_view(), name='audio_recorder'),
    path('translator/', TranscriptionDisplayView.as_view(), name='transcription'),
    path('event/list', EventListView.as_view(), name='event_list'),
    path('event/<slug:slug>/', EventDetailView.as_view(), name='event_detail'),
]