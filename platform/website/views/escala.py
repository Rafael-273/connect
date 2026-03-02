from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.http import Http404

from ..models.member import Member
from ..models.escala import Escala


@login_required
def minhas_escalas_view(request):
    """Exibe as escalas em que o usuário autenticado está escalado."""
    try:
        member = Member.objects.get(user=request.user, is_active=True)
    except Member.DoesNotExist:
        member = None

    if member is None:
        escalas = Escala.objects.none()
        user_ministries = []
    else:
        # Busca apenas as escalas do próprio membro (sem expor dados de outros)
        user_ministry_ids = member.ministry.values_list('id', flat=True)
        escalas = (
            Escala.objects
            .filter(members=member, ministry_id__in=user_ministry_ids)
            .select_related('ministry')
            .order_by('date')
        )
        user_ministries = list(member.ministry.all())

    return render(request, 'escalas/minhas.html', {
        'escalas': escalas,
        'member': member,
        'user_ministries': user_ministries,
    })


@login_required
def escalas_ministerio_view(request, ministry_id):
    """Exibe todas as escalas de um ministério — restrito aos membros desse ministério."""
    try:
        member = Member.objects.get(user=request.user, is_active=True)
    except Member.DoesNotExist:
        raise Http404

    # Verifica se o usuário pertence ao ministério solicitado
    if not member.ministry.filter(id=ministry_id).exists():
        raise Http404

    escalas = (
        Escala.objects
        .filter(ministry_id=ministry_id)
        .select_related('ministry')
        .prefetch_related('members')
        .order_by('date')
    )
    ministry = member.ministry.get(id=ministry_id)

    return render(request, 'escalas/ministerio.html', {
        'escalas': escalas,
        'ministry': ministry,
        'member': member,
    })
