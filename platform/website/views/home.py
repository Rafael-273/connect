from django.shortcuts import render
from django.views.generic import TemplateView
from django.utils import timezone
from ..models import Scale, Member


class HomeView(TemplateView):
    def get_template_names(self):
        if self.request.user.is_authenticated:
            return ['front/dashboard.html']
        return ['front/home.html']
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        if self.request.user.is_authenticated:
            try:
                member = Member.objects.get(user=self.request.user)
                context['member'] = member
                
                # Get upcoming scales for this user
                upcoming_scales = Scale.objects.filter(
                    member=member,
                    date__gte=timezone.now().date()
                ).select_related('ministry').order_by('date', 'start_time')[:5]
                
                context['upcoming_scales'] = upcoming_scales
                context['has_scales'] = upcoming_scales.exists()
                
            except Member.DoesNotExist:
                context['member'] = None
                context['upcoming_scales'] = []
                context['has_scales'] = False
        
        return context
