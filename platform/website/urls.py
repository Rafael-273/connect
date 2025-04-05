from django.urls import path
from .views.home import HomeView
from .views.visitor import VisitorCreateView

urlpatterns = [
    path('', HomeView.as_view(), name='home'),
    path('visitor/', VisitorCreateView.as_view(), name='visitor'),
]