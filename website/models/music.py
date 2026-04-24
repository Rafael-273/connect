from django.db import models
from ._base import BaseModel


class Music(BaseModel):
    TEMPO_CHOICES = [
        ('rapida', 'Rápida'),
        ('media', 'Média'),
        ('lenta', 'Lenta'),
    ]

    name = models.CharField(max_length=200)
    singer = models.CharField(max_length=200)
    chord_sheet = models.FileField(
        upload_to='chord_sheet/',
        null=True,
        blank=True
    )
    tempo = models.CharField(
        max_length=10,
        choices=TEMPO_CHOICES,
        blank=True,
        default='',
        verbose_name='Andamento',
    )

    def __str__(self):
        return f"{self.name} - {self.singer}"
