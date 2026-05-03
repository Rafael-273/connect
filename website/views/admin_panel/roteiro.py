import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from ..mixins import AdminRequiredMixin
from ...models.roteiro import AnuncioRoteiro, AnuncioRoteiroData, AnuncioRoteiroFoto, Roteiro


def _save_datas(anuncio, request):
    """Persist date/time rows submitted from the form."""
    datas = request.POST.getlist('data_evento')
    horas = request.POST.getlist('hora_evento')
    for data, hora in zip(datas, horas):
        data = data.strip()
        if data:
            AnuncioRoteiroData.objects.create(
                anuncio=anuncio,
                data=data,
                hora=hora.strip() or None,
            )


def _get_or_create_roteiro():
    roteiro, _ = Roteiro.objects.get_or_create(pk=1, defaults={'titulo': 'Roteiro de Culto'})
    return roteiro


class RoteiroView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Página principal do Roteiro de Culto."""

    def get(self, request):
        roteiro = _get_or_create_roteiro()
        anuncios = roteiro.anuncios.prefetch_related('datas', 'fotos').order_by('ordem', 'created_at')
        anuncios_ativos = [a for a in anuncios if not a.is_expired]
        return render(request, 'admin_panel/roteiro/detail.html', {
            'roteiro': roteiro,
            'anuncios': anuncios_ativos,
        })


class AnuncioCreateView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Criar um novo anúncio no roteiro."""

    def get(self, request):
        return render(request, 'admin_panel/roteiro/form.html')

    def post(self, request):
        roteiro = _get_or_create_roteiro()

        anuncio = AnuncioRoteiro(
            roteiro=roteiro,
            titulo=request.POST.get('titulo', '').strip(),
            descricao=request.POST.get('descricao', '').strip(),
            info_adicional=request.POST.get('info_adicional', '').strip(),
            ordem=roteiro.anuncios.count(),
        )

        data_exp = request.POST.get('data_expiracao', '').strip()
        if data_exp:
            anuncio.data_expiracao = data_exp

        if 'foto' in request.FILES:
            anuncio.foto = request.FILES['foto']

        anuncio.save()

        # Save multiple photos
        fotos = request.FILES.getlist('fotos')
        for i, foto in enumerate(fotos):
            AnuncioRoteiroFoto.objects.create(anuncio=anuncio, imagem=foto, ordem=i)

        _save_datas(anuncio, request)
        messages.success(request, 'Anúncio adicionado com sucesso!')
        return redirect('roteiro_view')


class AnuncioEditView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Editar um anúncio existente."""

    def get(self, request, anuncio_id):
        roteiro = _get_or_create_roteiro()
        anuncio = get_object_or_404(AnuncioRoteiro, id=anuncio_id, roteiro=roteiro)
        return render(request, 'admin_panel/roteiro/form.html', {
            'anuncio': anuncio,
        })

    def post(self, request, anuncio_id):
        roteiro = _get_or_create_roteiro()
        anuncio = get_object_or_404(AnuncioRoteiro, id=anuncio_id, roteiro=roteiro)

        anuncio.titulo = request.POST.get('titulo', '').strip()
        anuncio.descricao = request.POST.get('descricao', '').strip()
        anuncio.info_adicional = request.POST.get('info_adicional', '').strip()

        data_exp = request.POST.get('data_expiracao', '').strip()
        anuncio.data_expiracao = data_exp if data_exp else None

        if 'foto' in request.FILES:
            anuncio.foto = request.FILES['foto']
        elif request.POST.get('remove_foto'):
            anuncio.foto = None

        anuncio.save()

        # Handle existing photo removal
        remove_foto_ids = request.POST.getlist('remove_foto_ids')
        if remove_foto_ids:
            AnuncioRoteiroFoto.objects.filter(id__in=remove_foto_ids, anuncio=anuncio).delete()

        # Save new multiple photos
        fotos = request.FILES.getlist('fotos')
        for i, foto in enumerate(fotos):
            AnuncioRoteiroFoto.objects.create(anuncio=anuncio, imagem=foto, ordem=anuncio.fotos.count() + i)

        anuncio.datas.all().delete()
        _save_datas(anuncio, request)
        messages.success(request, 'Anúncio atualizado com sucesso!')
        return redirect('roteiro_view')


class AnuncioDeleteView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Excluir um anúncio do roteiro."""

    def post(self, request, anuncio_id):
        roteiro = _get_or_create_roteiro()
        anuncio = get_object_or_404(AnuncioRoteiro, id=anuncio_id, roteiro=roteiro)
        anuncio.delete()
        messages.success(request, 'Anúncio removido com sucesso!')
        return redirect('roteiro_view')


class AnuncioReorderView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Reordenar anúncios via drag-and-drop (JSON POST)."""

    def post(self, request):
        try:
            data = json.loads(request.body)
            order = data.get('order', [])  # list of ids in new order
            roteiro = _get_or_create_roteiro()
            for idx, anuncio_id in enumerate(order):
                AnuncioRoteiro.objects.filter(id=anuncio_id, roteiro=roteiro).update(ordem=idx)
            return JsonResponse({'success': True})
        except Exception as exc:
            return JsonResponse({'success': False, 'error': str(exc)}, status=400)


class RoteiroPrintView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Página de impressão / PDF do roteiro."""

    def get(self, request):
        roteiro = _get_or_create_roteiro()
        anuncios = (
            roteiro.anuncios
            .prefetch_related('datas', 'fotos')
            .order_by('ordem', 'created_at')
        )
        # Filter out expired items for print
        anuncios_ativos = [a for a in anuncios if not a.is_expired]
        return render(request, 'admin_panel/roteiro/print.html', {
            'roteiro': roteiro,
            'anuncios': anuncios_ativos,
        })
