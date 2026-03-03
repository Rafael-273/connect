from django.db import models
from ._base import BaseModel


class PrayerRequest(BaseModel):
    name = models.CharField(max_length=150, blank=True, null=True, verbose_name='Nome')
    content = models.TextField(verbose_name='Pedido de oração')

    class Meta:
        verbose_name = 'Pedido de Oração'
        verbose_name_plural = 'Pedidos de Oração'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name or 'Anônimo'} — {self.created_at.strftime('%d/%m/%Y')}"
