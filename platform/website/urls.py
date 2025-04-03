from django.urls import path
from .views.home import HomeView
from .views.engage import CoachingView

urlpatterns = [
    path('', HomeView.as_view(), name='home'),
    path('coaching/', CoachingView.as_view(), name='coaching')
]