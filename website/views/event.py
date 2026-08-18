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
        event_type = self.request.GET.get('type', '')  # 'normal', 'recurring', ou vazio (todos)
        today = timezone.now().date()
        
        # Eventos especiais só aparecem enquanto estão visíveis e possuem pelo
        # menos uma data de realização hoje ou no futuro. A data principal pode
        # já ter passado quando o evento possui datas adicionais em `dates`.
        queryset = (
            Event.objects.filter(
                is_recurring=False,
                display_start__lte=today,
                display_end__gte=today,
            )
            .filter(
                Q(event_date__gte=today) | Q(dates__event_date__gte=today)
            )
            .prefetch_related('dates')
            .distinct()
            .order_by('event_date')
        )
        
        # Filtrar por tipo se especificado
        if event_type == 'normal':
            queryset = queryset.filter(is_recurring=False)
        elif event_type == 'recurring':
            # Se filtrar por recorrente, não retorna nada aqui (será tratado no contexto)
            queryset = Event.objects.none()

        if query:
            queryset = queryset.filter(
                Q(title__icontains=query) |
                Q(description__icontains=query) |
                Q(location__icontains=query)
            )

        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['navbar_transparent'] = True
        
        query = self.request.GET.get('q', '')
        event_type = self.request.GET.get('type', '')
        
        # Adicionar eventos recorrentes (também filtrados pela pesquisa se houver)
        recurring_queryset = Event.objects.filter(is_recurring=True).prefetch_related('dates')
        
        # Se filtrar por tipo 'normal', não mostra recorrentes
        if event_type == 'normal':
            recurring_queryset = Event.objects.none()
        
        if query:
            recurring_queryset = recurring_queryset.filter(
                Q(title__icontains=query) |
                Q(description__icontains=query) |
                Q(location__icontains=query)
            )
        
        context['recurring_events'] = recurring_queryset
        context['search_query'] = query
        context['event_type'] = event_type
        
        return context


class EventDetailView(DetailView):
    model = Event
    template_name = 'front/event.html'
    context_object_name = 'event'

    def get_queryset(self):
        return Event.objects.prefetch_related('dates')
    
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
