from django.views.generic.edit import CreateView
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
