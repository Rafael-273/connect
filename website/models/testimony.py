from django.db import models
from ._base import BaseModel


class Testimony(BaseModel):
    """Modelo para armazenar testemunhos da igreja"""

    CATEGORY_CHOICES = [
        ('healing', 'Cura'),
        ('salvation', 'Salvação'),
        ('deliverance', 'Libertação'),
        ('provision', 'Provisão'),
        ('restoration', 'Restauração'),
        ('other', 'Outro'),
    ]

    author_name = models.CharField(
        max_length=150,
        verbose_name='Nome do Autor',
        blank=True,
        default=''
    )
    member = models.ForeignKey(
        'Member',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='testimonies',
        verbose_name='Membro'
    )
    photo = models.ImageField(
        upload_to='testimonies/',
        blank=True,
        null=True,
        verbose_name='Foto'
    )
    title = models.CharField(
        max_length=255,
        verbose_name='Título do Testemunho'
    )
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        default='other',
        verbose_name='Categoria'
    )
    is_approved = models.BooleanField(
        default=False,
        verbose_name='Aprovado',
        help_text='Testemunho aprovado para exibição no site'
    )
    show_on_home = models.BooleanField(
        default=False,
        verbose_name='Exibir na Home',
        help_text='Exibir este testemunho na página inicial'
    )
    testimony_date = models.DateField(
        verbose_name='Data do Testemunho',
        blank=True,
        null=True
    )
    instagram_url = models.URLField(
        max_length=500,
        blank=True,
        null=True,
        verbose_name='URL do Instagram',
        help_text='Cole a URL do post/reel do Instagram para embed de vídeo'
    )

    class Meta:
        verbose_name = 'Testemunho'
        verbose_name_plural = 'Testemunhos'
        ordering = ['-created_at']

    @property
    def instagram_embed_url(self):
        """Returns the embed URL for the Instagram post/reel."""
        if self.instagram_url:
            url = self.instagram_url.rstrip('/')
            return f"{url}/embed/"
        return ''

    def __str__(self):
        return f"{self.title} - {self.get_category_display()}"
