from django.db import models
from ._base import BaseModel
from .ministry import Ministry
from .member import Member


class Schedule(BaseModel):
    STATUS_CHOICES = [
        ('active', 'Ativo'),
        ('cancelled', 'Cancelado'),
        ('completed', 'Realizado'),
    ]
    
    ministry = models.ForeignKey(Ministry, on_delete=models.CASCADE, related_name='schedules')
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='schedules')
    date = models.DateField()
    role = models.CharField(max_length=100, blank=True, help_text='Função/papel na escala')
    notes = models.TextField(blank=True, null=True, help_text='Observações ou instruções especiais')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    
    class Meta:
        ordering = ['-date']
        unique_together = ['ministry', 'member', 'date']
    
    def __str__(self):
        return f"{self.member.name} - {self.ministry.name} - {self.date}"
    
    @property
    def is_active(self):
        return self.status == 'active'