from django.views.generic import ListView
from django.views.generic.edit import CreateView
from django.db.models import Q
from django.utils.dateparse import parse_date
from datetime import timedelta
from django.urls import reverse_lazy
from django.utils import timezone
from ..models.visitor import Visitor
from ..forms.visitor import VisitorForm


class VisitorCreateView(CreateView):
    model = Visitor
    form_class = VisitorForm
    template_name = 'create/visitor.html'
    success_url = reverse_lazy('home')

    def form_valid(self, form):
        form.instance.visit_date = timezone.now()
        return super().form_valid(form)


class VisitorListView(ListView):
    model = Visitor
    template_name = 'list/visitor.html'
    context_object_name = 'visitors'

    def get_queryset(self):
        queryset = super().get_queryset()
        query = self.request.GET.get('q', '')
        visit_data = self.request.GET.get('visit_data', '')

        # Filtro por nome, email ou telefone
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query) |
                Q(email__icontains=query) |
                Q(phone__icontains=query)
            )

        # Filtro por data de visita
        if visit_data:
            visit_data_parsed = parse_date(visit_data)  # Converte a string para um objeto date
            if visit_data_parsed:
                # Filtra pela data exata (dia inteiro)
                start_of_day = visit_data_parsed
                end_of_day = visit_data_parsed + timedelta(days=1)  # Um dia após para incluir todo o dia
                queryset = queryset.filter(visit_date__gte=start_of_day, visit_date__lt=end_of_day)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['query'] = self.request.GET.get('q', '')
        context['visit_data'] = self.request.GET.get('visit_data', '')
        return context