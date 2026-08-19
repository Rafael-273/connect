from django.db import models

from ._base import BaseModel
from .media_content import CONTENT_TYPE_CHOICES, PUBLICATION_CHANNEL_CHOICES


class MediaEventType(BaseModel):
    """Classificação de eventos da igreja para planejamento de mídia."""

    name = models.CharField(max_length=100, unique=True, verbose_name='Nome')
    description = models.TextField(blank=True, verbose_name='Descrição')
    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name='Ordem')

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name = 'Tipo de Evento (Mídia)'
        verbose_name_plural = 'Tipos de Evento (Mídia)'

    def __str__(self):
        return self.name


class MediaPlanningTemplate(BaseModel):
    """Template de mídia associado a um tipo de evento."""

    event_type = models.OneToOneField(
        MediaEventType,
        on_delete=models.CASCADE,
        related_name='planning_template',
        verbose_name='Tipo de evento',
    )
    name = models.CharField(max_length=150, verbose_name='Nome do template')
    description = models.TextField(blank=True, verbose_name='Descrição')
    is_active = models.BooleanField(default=True, verbose_name='Ativo')

    class Meta:
        db_table = 'website_media_planning_template'
        verbose_name = 'Template de Mídia'
        verbose_name_plural = 'Templates de Mídia'

    def __str__(self):
        return self.name


class MediaPlanningTemplateItem(BaseModel):
    """Item sugerido dentro de um template de mídia."""

    template = models.ForeignKey(
        MediaPlanningTemplate,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='Template',
    )
    title = models.CharField(max_length=200, verbose_name='Nome da demanda')
    content_type = models.CharField(
        max_length=20,
        choices=CONTENT_TYPE_CHOICES,
        verbose_name='Tipo de conteúdo',
    )
    description = models.TextField(blank=True, verbose_name='Descrição')
    default_sub_team = models.ForeignKey(
        'MediaSubTeam',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='template_items',
        verbose_name='Equipe normalmente responsável',
    )
    default_role = models.ForeignKey(
        'MediaRole',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='template_items',
        verbose_name='Função normalmente responsável',
    )
    requires_recording = models.BooleanField(default=False, verbose_name='Necessita gravação?')
    requires_editing = models.BooleanField(default=False, verbose_name='Necessita edição?')
    publication_offset_days = models.IntegerField(
        null=True,
        blank=True,
        verbose_name='Publicação (dias em relação ao evento)',
        help_text='Negativo = antes do evento. Ex: -14 = 14 dias antes.',
    )
    due_offset_days = models.IntegerField(
        null=True,
        blank=True,
        verbose_name='Prazo (dias em relação ao evento)',
        help_text='Negativo = antes do evento. Positivo = depois. Ex: 3 = 3 dias após.',
    )
    lead_offset_days = models.IntegerField(
        null=True,
        blank=True,
        verbose_name='Antecedência recomendada (dias antes do evento)',
        help_text='Quando iniciar o trabalho, em dias antes do evento.',
    )
    publication_channel = models.CharField(
        max_length=30,
        choices=PUBLICATION_CHANNEL_CHOICES,
        blank=True,
        verbose_name='Canal de publicação',
    )
    notes = models.TextField(blank=True, verbose_name='Observações')
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name='Ordem')

    class Meta:
        db_table = 'website_media_planning_template_item'
        ordering = ['sort_order', 'title']
        verbose_name = 'Item de Template'
        verbose_name_plural = 'Itens de Template'

    def __str__(self):
        return self.title
