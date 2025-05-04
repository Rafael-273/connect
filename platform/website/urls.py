from django.urls import path
from .views.home import HomeView
from .views.visitor import VisitorCreateView, VisitorListView
from .views.member import MemberCreateView

urlpatterns = [
    path('', HomeView.as_view(), name='home'),
    path('visitor/', VisitorCreateView.as_view(), name='visitor'),
    path('visitor_list/', VisitorListView.as_view(), name='visitor_list'),
    path('member_register/', MemberCreateView.as_view(), name='member_register'),
]