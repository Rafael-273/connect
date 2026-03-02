from django.db import models
from ._base import BaseModel
from .member import Member
from .ministry import Ministry


class Scale(BaseModel):
    STATUS_CHOICES = [
        ('confirmed', 'Confirmada'),
        ('pending', 'Pendente'),
        ('cancelled', 'Cancelada'),
    ]
    
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='scales')
    ministry = models.ForeignKey(Ministry, on_delete=models.CASCADE, related_name='scales')
    date = models.DateField()
    start_time = models.TimeField(blank=True, null=True)
    end_time = models.TimeField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='confirmed')
    notes = models.TextField(blank=True, null=True)
    
    class Meta:
        ordering = ['date', 'start_time']
        unique_together = ['member', 'ministry', 'date']
    
    def __str__(self):
        return f"{self.member.name} - {self.ministry.name} ({self.date})"