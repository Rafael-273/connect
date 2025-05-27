from django.urls import path
from .views.home import HomeView
from .views.visitor import VisitorCreateView, VisitorListView
from .views.member import MemberCreateView
from .views.translator import AudioRecorderView

urlpatterns = [
    path('', HomeView.as_view(), name='home'),
    path('visitor/', VisitorCreateView.as_view(), name='visitor'),
    path('visitor/list/', VisitorListView.as_view(), name='visitor_list'),
    path('member/register/', MemberCreateView.as_view(), name='member_register'),
    path('translator/recorder/', AudioRecorderView.as_view(), name='audio_recorder'),
]