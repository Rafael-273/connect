from django.views.generic import ListView, DetailView
from django.db.models import Q
from django.utils import timezone
from ..models.event import Event


class EventListView(ListView):
    model = Event
    template_name = 'list/event.html'
    context_object_name = 'events'
    paginate_by = 10

    def get_queryset(self):
        query = self.request.GET.get('q', '')
        today = timezone.now().date()
        
        queryset = Event.objects.all()

        if query:
            queryset = queryset.filter(
                Q(title__icontains=query) |
                Q(description__icontains=query)
            )

        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['navbar_transparent'] = True
        return context


class EventDetailView(DetailView):
    model = Event
    template_name = 'front/event.html'
    context_object_name = 'event'
    
    def get_object(self, queryset=None):
        # First get the regular object
        obj = super().get_object(queryset)
        
        # Check if it's visible based on display dates
        today = timezone.now().date()
        if not (obj.display_start <= today <= obj.display_end):
            from django.http import Http404
            raise Http404("Este evento não está disponível no momento.")
            
        return obj
