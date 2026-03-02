from django.db import models
from ._base import BaseModel
from .ministry import Ministry
from .member import Member


class Escala(BaseModel):
    """Model for ministry schedules/rosters"""
    
    ministry = models.ForeignKey(Ministry, on_delete=models.CASCADE, related_name='escalas')
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='escalas')
    title = models.CharField(max_length=200, help_text="Título da escala (ex: Louvor, Portaria, etc)")
    date = models.DateField(help_text="Data da escala")
    description = models.TextField(blank=True, null=True, help_text="Descrição adicional da escala")
    
    # Optional fields for more specific scheduling
    start_time = models.TimeField(blank=True, null=True, help_text="Horário de início")
    end_time = models.TimeField(blank=True, null=True, help_text="Horário de fim")
    is_confirmed = models.BooleanField(default=False, help_text="Se a pessoa confirmou participação")
    
    class Meta:
        ordering = ['-date', 'start_time']
        verbose_name = "Escala"
        verbose_name_plural = "Escalas"
    
    def __str__(self):
        return f"{self.title} - {self.member.name} ({self.date})"