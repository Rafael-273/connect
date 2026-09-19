from django.db import models

from ._base import BaseModel
from .media_content import CONTENT_TYPE_CHOICES

MONTH_NAMES_PT = [
    '', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
    'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro',
]


class MediaContentCategory(BaseModel):
    """Categoria reutilizável para conteúdos avulsos (ex: Devocional Semanal)."""
    name = models.CharField(max_length=100, verbose_name='Nome')
    content_type = models.CharField(
        max_length=20,
        choices=CONTENT_TYPE_CHOICES,
        verbose_name='Tipo padrão',
    )
    description = models.TextField(blank=True, verbose_name='Descrição')

    class Meta:
        ordering = ['name']
        verbose_name = 'Categoria de Conteúdo'
        verbose_name_plural = 'Categorias de Conteúdo'

    def __str__(self):
        return self.name


class MediaMonthPlan(BaseModel):
    """Planejamento de mídia para um mês específico."""
    year = models.PositiveSmallIntegerField(verbose_name='Ano')
    month = models.PositiveSmallIntegerField(verbose_name='Mês')  # 1–12
    events = models.ManyToManyField(
        'Event',
        blank=True,
        related_name='month_plans',
        verbose_name='Eventos',
    )
    categories = models.ManyToManyField(
        MediaContentCategory,
        blank=True,
        related_name='month_plans',
        verbose_name='Categorias',
    )

    class Meta:
        unique_together = [('year', 'month')]
        ordering = ['-year', '-month']
        verbose_name = 'Plano Mensal de Mídia'
        verbose_name_plural = 'Planos Mensais de Mídia'

    def __str__(self):
        return f"{MONTH_NAMES_PT[self.month]} {self.year}"

    @property
    def month_name(self):
        return MONTH_NAMES_PT[self.month]
