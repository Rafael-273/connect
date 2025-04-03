from django.db import models
from ._base import BaseModel
from .member import Member


class FollowUp(BaseModel):
    FOLLOWUP_STATUS = [
        ('in_progress', 'Em Progresso'),
        ('completed', 'Completo'),
    ]

    discipled_consolidated = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="received_followups")
    discipler_consolidator = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="performed_followups")
    start_date = models.DateField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=FOLLOWUP_STATUS, default='in_progress')
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.discipled_consolidated.name} - {self.discipler_consolidator.name}"