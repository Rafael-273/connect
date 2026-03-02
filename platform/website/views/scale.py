from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views.generic import ListView
from django.db.models import Q
from django.utils import timezone
from ..models import Scale, Ministry, Member


@method_decorator(login_required, name='dispatch')
class MinhasEscalasView(ListView):
    model = Scale
    template_name = 'front/minhas_escalas.html'
    context_object_name = 'escalas'
    paginate_by = 20
    
    def get_queryset(self):
        # Get the member associated with the current user
        try:
            member = Member.objects.get(user=self.request.user)
            # Return scales for this member, ordered by date (closest first)
            return Scale.objects.filter(
                member=member,
                date__gte=timezone.now().date()
            ).select_related('ministry').order_by('date', 'start_time')
        except Member.DoesNotExist:
            return Scale.objects.none()
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            member = Member.objects.get(user=self.request.user)
            context['member'] = member
            # Also get past escalas for reference
            context['escalas_passadas'] = Scale.objects.filter(
                member=member,
                date__lt=timezone.now().date()
            ).select_related('ministry').order_by('-date', '-start_time')[:5]
        except Member.DoesNotExist:
            context['member'] = None
            context['escalas_passadas'] = []
        return context


@method_decorator(login_required, name='dispatch')
class EscalasMinisterioView(ListView):
    model = Scale
    template_name = 'front/escalas_ministerio.html'
    context_object_name = 'escalas'
    paginate_by = 30
    
    def get_queryset(self):
        ministry_id = self.kwargs.get('ministry_id')
        return Scale.objects.filter(
            ministry_id=ministry_id,
            date__gte=timezone.now().date()
        ).select_related('member', 'ministry').order_by('date', 'start_time')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        ministry_id = self.kwargs.get('ministry_id')
        context['ministry'] = get_object_or_404(Ministry, id=ministry_id)
        
        # Highlight current user's scales
        try:
            member = Member.objects.get(user=self.request.user)
            context['user_member'] = member
            context['user_scales'] = [escala.id for escala in context['escalas'] if escala.member == member]
        except Member.DoesNotExist:
            context['user_member'] = None
            context['user_scales'] = []
            
        return context