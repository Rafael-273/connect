import uuid
from django.contrib.auth import get_user_model
from django.views.generic.edit import CreateView
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