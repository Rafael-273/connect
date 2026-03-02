from django.db import models
from ._base import BaseModel
from .ministry import Ministry
from .member import Member


class Escala(BaseModel):
    title = models.CharField(max_length=200, verbose_name='Título')
    date = models.DateField(verbose_name='Data')
    ministry = models.ForeignKey(
        Ministry,
        on_delete=models.CASCADE,
        related_name='escalas',
        verbose_name='Ministério',
    )
    members = models.ManyToManyField(
        Member,
        blank=True,
        related_name='escalas',
        verbose_name='Membros Escalados',
    )
    description = models.TextField(blank=True, null=True, verbose_name='Descrição')

    class Meta:
        ordering = ['date']
        verbose_name = 'Escala'
        verbose_name_plural = 'Escalas'

    def __str__(self):
        return f'{self.title} - {self.ministry.name} ({self.date})'
