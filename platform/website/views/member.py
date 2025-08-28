import uuid
from django.contrib.auth import get_user_model
from django.views.generic.edit import CreateView
from django.views.generic import ListView
from django.urls import reverse_lazy
from ..models.member import Member
from ..forms.member import MemberForm
from ..models.user import User

User = get_user_model()


class MemberCreateView(CreateView):
    model = Member
    form_class = MemberForm
    template_name = 'create/member.html'
    success_url = reverse_lazy('home')

    def form_valid(self, form):
        email = self.request.POST.get('email')
        if not email:
            email = f"{uuid.uuid4().hex[:10]}@autogerado.com"

        user = User.objects.create_user(
            email=email,
            password='senha_padrão'
        )

        member = form.save(commit=False)
        member.user = user
        member.save()

        self.object = member

        return super().form_valid(form)
    

class NewConvertsListView(ListView):
    model = Member
    template_name = 'list/new_converts.html'
    context_object_name = 'members'

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.order_by('-created_at')
        query = self.request.GET.get('q', '')
        queryset = queryset.filter(conversion='new_convert')
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query) |
                Q(phone__icontains=query)
            )
        return queryset

    def normalize_phone_number(self, phone, default_ddd='21'):
        import re
        raw_phone = re.sub(r'\D', '', phone or '')
        if len(raw_phone) == 8 or len(raw_phone) == 9:
            return default_ddd + raw_phone
        elif len(raw_phone) == 10 or len(raw_phone) == 11:
            return raw_phone
        elif raw_phone.startswith('55') and len(raw_phone) >= 12:
            return raw_phone[2:]
        else:
            return raw_phone

    def clean_members(self, members):
        for member in members:
            member.cleaned_phone = self.normalize_phone_number(member.phone)
            if member.name:
                member.name = member.name.title()
                member.first_name = member.name.split()[0]
            else:
                member.first_name = ''
        return members

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        members = context['members']
        for member in members:
            member.cleaned_phone = self.normalize_phone_number(member.phone)
        context['members'] = self.clean_members(members)
        context['query'] = self.request.GET.get('q', '')
        return context