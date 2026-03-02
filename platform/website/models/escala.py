from django.db import models
from ._base import BaseModel
from .ministry import Ministry
from .member import Member


class Escala(BaseModel):
    ministry = models.ForeignKey(Ministry, on_delete=models.CASCADE, related_name='escalas')
    date = models.DateField(verbose_name='Data')
    title = models.CharField(max_length=200, verbose_name='Título')
    description = models.TextField(blank=True, null=True, verbose_name='Descrição')
    assigned_members = models.ManyToManyField(Member, blank=True, verbose_name='Membros Escalados')
    
    class Meta:
        ordering = ['date']
        verbose_name = 'Escala'
        verbose_name_plural = 'Escalas'
    
    def __str__(self):
        return f"{self.title} - {self.ministry.name} ({self.date.strftime('%d/%m/%Y')})"
    
    @property
    def is_past(self):
        from django.utils import timezone
        return self.date < timezone.now().date()