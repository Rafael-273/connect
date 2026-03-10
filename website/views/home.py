from django.views.generic import TemplateView
from website.models import Testimony


class HomeView(TemplateView):
    template_name = 'front/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['testimonies'] = Testimony.objects.filter(
            is_approved=True,
            show_on_home=True
        )[:6]
        return context


class TestimonyListView(TemplateView):
    template_name = 'front/testemunhos.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['testimonies'] = Testimony.objects.filter(is_approved=True)
        context['navbar_transparent'] = True
        return context


class ContactView(TemplateView):
    template_name = 'front/contato.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['navbar_transparent'] = True
        return context
