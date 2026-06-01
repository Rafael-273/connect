from django.db import models

from ._base import BaseModel

CONTENT_TYPE_CHOICES = [
    ('artwork', 'Arte'),
    ('reel', 'Reel'),
    ('carousel', 'Carrossel'),
    ('story', 'Story'),
    ('photography', 'Fotografia'),
    ('video', 'Vídeo'),
    ('livestream', 'Transmissão ao Vivo'),
    ('youtube', 'YouTube'),
    ('whatsapp', 'WhatsApp'),
    ('website_banner', 'Banner do Site'),
    ('devotional', 'Devocional'),
    ('announcement', 'Comunicado'),
]

STATUS_CHOICES = [
    ('pending', 'Pendente'),
    ('in_progress', 'Em Produção'),
    ('review', 'Em Revisão'),
    ('approved', 'Aprovado'),
    ('scheduled', 'Agendado'),
    ('published', 'Publicado'),
]

PRIORITY_CHOICES = [
    ('low', 'Baixa'),
    ('medium', 'Média'),
    ('high', 'Alta'),
    ('urgent', 'Urgente'),
]


class MediaContent(BaseModel):
    title = models.CharField(max_length=200, verbose_name='Título')
    description = models.TextField(blank=True, verbose_name='Descrição')
    content_type = models.CharField(
        max_length=20,
        choices=CONTENT_TYPE_CHOICES,
        verbose_name='Tipo de Conteúdo',
    )
    event = models.ForeignKey(
        'Event',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='media_contents',
        verbose_name='Evento',
    )
    publication_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Data de Publicação',
    )
    due_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Prazo de Entrega',
    )
    responsible = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='media_contents_responsible',
        verbose_name='Responsável',
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='Status',
    )
    priority = models.CharField(
        max_length=10,
        choices=PRIORITY_CHOICES,
        default='medium',
        verbose_name='Prioridade',
    )
    month_plan = models.ForeignKey(
        'MediaMonthPlan',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='contents',
        verbose_name='Plano Mensal',
    )
    category = models.ForeignKey(
        'MediaContentCategory',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='contents',
        verbose_name='Categoria',
    )

    class Meta:
        verbose_name = 'Conteúdo de Mídia'
        verbose_name_plural = 'Conteúdos de Mídia'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} ({self.get_content_type_display()})"
