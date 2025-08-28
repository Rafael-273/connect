from django.db import models
from ._base import BaseModel
from .member import Member


class FollowUp(BaseModel):
    accompanied = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="received_followups")
    responsible = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="performed_followups")
    end_date = models.DateField(blank=True, null=True)
    profile_notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.accompanied.name} - {self.responsible.name}"


class FollowUpReport(BaseModel):
    STATUS_CHOICES = [
        ('progressing', 'Progredindo'),
        ('needs_support', 'Preciso de ajuda'),
        ('urgent_help', 'Preciso de ajuda urgente'),
        ('not_progressing', 'Não estou conseguindo progredir'),
        ('inactive', 'Sem sinal de vida'),
    ]

    FOLLOWUP_TYPE_CHOICES = [
        ('consolidation', 'Consolidação'),
        ('discipleship', 'Discipulado'),
    ]

    followup = models.ForeignKey('FollowUp', on_delete=models.CASCADE, related_name='reports')
    type = models.CharField(max_length=20, choices=FOLLOWUP_TYPE_CHOICES)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES)
    location = models.CharField(max_length=255)
    description = models.TextField()
    prayer_request = models.TextField(blank=True, null=True)
    date = models.DateField(auto_now_add=True)

    def __str__(self):
        return f"{self.followup.accompanied.name} - {self.get_type_display()} - {self.get_status_display()} ({self.date})"
