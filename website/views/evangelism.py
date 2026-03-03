from django.views.generic.edit import CreateView
from django.urls import reverse_lazy
from django.utils import timezone
from ..models.evangelism import Evangelized
from ..forms.evangelism import EvangelizedForm


class EvangelismCreateView(CreateView):
    model = Evangelized
    form_class = EvangelizedForm
    template_name = 'create/evangelism.html'
    success_url = reverse_lazy('home')

    def form_valid(self, form):
        form.instance.evangelism_date = timezone.now()
        return super().form_valid(form)
