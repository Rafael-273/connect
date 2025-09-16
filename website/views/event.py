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
        
        # Filtramos apenas eventos NÃO recorrentes
        queryset = Event.objects.filter(is_recurring=False)

        if query:
            queryset = queryset.filter(
                Q(title__icontains=query) |
                Q(description__icontains=query)
            )

        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['navbar_transparent'] = True
        
        # Adicionar eventos recorrentes separadamente
        context['recurring_events'] = Event.objects.filter(is_recurring=True)
        
        return context


class EventDetailView(DetailView):
    model = Event
    template_name = 'front/event.html'
    context_object_name = 'event'
    
    def get_object(self, queryset=None):
        # First get the regular object
        obj = super().get_object(queryset)
        
        # Check if it's visible based on display dates or if it's recurring
        if obj.is_recurring:
            # Eventos recorrentes são sempre visíveis
            return obj
            
        # Para eventos não recorrentes, verificar datas de exibição
        today = timezone.now().date()
        if not (obj.display_start <= today <= obj.display_end):
            from django.http import Http404
            raise Http404("Este evento não está disponível no momento.")
            
        return obj
