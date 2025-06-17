from django.views.generic import ListView, DetailView
from django.db.models import Q
from ..models.event import Event


class EventListView(ListView):
    model = Event
    template_name = 'list/event.html'
    context_object_name = 'events'
    paginate_by = 10

    def get_queryset(self):
        query = self.request.GET.get('q', '')
        queryset = Event.objects.all().order_by('-event_date')

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
