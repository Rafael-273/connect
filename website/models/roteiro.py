from django.db import models
from django.utils import timezone

from ._base import BaseModel


class Roteiro(BaseModel):
    """Roteiro de Culto — instância única global."""
    titulo = models.CharField(max_length=255, default='Roteiro de Culto')

    class Meta:
        verbose_name = 'Roteiro de Culto'
        verbose_name_plural = 'Roteiros de Culto'

    def __str__(self):
        return self.titulo


class AnuncioRoteiro(BaseModel):
    """Um anúncio/item dentro do Roteiro de Culto."""
    roteiro = models.ForeignKey(
        Roteiro, on_delete=models.CASCADE, related_name='anuncios'
    )

    titulo = models.CharField(max_length=255, blank=True, verbose_name='Título')
    foto = models.ImageField(
        upload_to='roteiro/', blank=True, null=True, verbose_name='Foto'
    )
    descricao = models.TextField(blank=True, verbose_name='Descrição')
    data_expiracao = models.DateField(
        null=True, blank=True, verbose_name='Data de expiração'
    )

    # Sempre disponível
    info_adicional = models.TextField(blank=True, verbose_name='Informação adicional')

    ordem = models.PositiveIntegerField(default=0, verbose_name='Ordem')

    class Meta:
        ordering = ['ordem', 'created_at']
        verbose_name = 'Anúncio do Roteiro'
        verbose_name_plural = 'Anúncios do Roteiro'

    def __str__(self):
        return self.titulo or f'Anúncio #{self.pk}'

    @property
    def is_expired(self):
        today = timezone.now().date()
        datas = list(self.datas.values_list('data', flat=True))
        if datas:
            # If the last scheduled date is today or earlier, it should no longer appear.
            return max(datas) <= today
        return bool(self.data_expiracao and self.data_expiracao <= today)

    @property
    def imagens(self):
        """Retorna todas as fotos do anúncio (novo campo + legado)."""
        fotos = list(self.fotos.all().order_by('ordem', 'created_at'))
        # Incluir a foto legada se existir
        if self.foto:
            return [self.foto] + [f.imagem for f in fotos]
        return [f.imagem for f in fotos]


class AnuncioRoteiroData(models.Model):
    """Uma data/horário de realização de um anúncio (suporta multi-dia)."""
    anuncio = models.ForeignKey(
        AnuncioRoteiro, on_delete=models.CASCADE, related_name='datas'
    )
    data = models.DateField(verbose_name='Data')
    hora = models.TimeField(null=True, blank=True, verbose_name='Hora')

    class Meta:
        ordering = ['data', 'hora']
        verbose_name = 'Data do Anúncio'
        verbose_name_plural = 'Datas do Anúncio'

    def __str__(self):
        s = str(self.data)
        if self.hora:
            s += f' às {self.hora.strftime("%H:%M")}'
        return s


class AnuncioRoteiroFoto(BaseModel):
    """Múltiplas imagens para um anúncio do roteiro."""
    anuncio = models.ForeignKey(
        AnuncioRoteiro, on_delete=models.CASCADE, related_name='fotos'
    )
    imagem = models.ImageField(upload_to='roteiro/', verbose_name='Imagem')
    ordem = models.PositiveIntegerField(default=0, verbose_name='Ordem')

    class Meta:
        ordering = ['ordem', 'created_at']
        verbose_name = 'Foto do Anúncio'
        verbose_name_plural = 'Fotos do Anúncio'

    def __str__(self):
        return f"Foto #{self.pk} de {self.anuncio.titulo or 'Anúncio ' + str(self.anuncio.pk)}"