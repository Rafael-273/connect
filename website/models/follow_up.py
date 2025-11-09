from django.db import models
from django.utils import timezone
from ._base import BaseModel
from .member import Member
from datetime import timedelta


class FollowUpTemplate(BaseModel):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name


class FollowUpTemplateStep(BaseModel):
    template = models.ForeignKey(FollowUpTemplate, on_delete=models.CASCADE, related_name="steps")
    week = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["week", "id"]

    def __str__(self):
        return f"Semana {self.week} - {self.title}"


class FollowUp(BaseModel):
    accompanied = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="received_followups")
    responsible = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="performed_followups")
    template = models.ForeignKey(FollowUpTemplate, on_delete=models.SET_NULL, null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(blank=True, null=True)
    profile_notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.accompanied.name} - {self.responsible.name}"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        
        if not self.start_date:
            self.start_date = timezone.now().date()
            
        super().save(*args, **kwargs)

        # Só criar etapas se for novo e tiver template
        if is_new and self.template:
            start_date = self.start_date or timezone.now().date()
            
            for step in self.template.steps.all():
                due_date = start_date + timedelta(weeks=step.week - 1)
                FollowUpStep.objects.create(
                    followup=self,
                    week=step.week,
                    title=step.title,
                    description=step.description,
                    due_date=due_date
                )

    @property
    def progress_percentage(self):
        total_steps = self.steps.count()
        if total_steps == 0:
            return 0
        completed_steps = self.steps.filter(completed=True).count()
        return int((completed_steps / total_steps) * 100)

    @property
    def completed_steps(self):
        return self.steps.filter(completed=True).count()

    @property
    def total_steps(self):
        return self.steps.count()

    @property
    def is_completed(self):
        return self.steps.exists() and not self.steps.filter(completed=False).exists()
    
    @property
    def is_overdue(self):
        """Verifica se há etapas vencidas"""
        return self.steps.filter(due_date__lt=timezone.now().date(), completed=False).exists()
    
    @property
    def last_contact(self):
        """Data do último relatório/contato"""
        latest_report = self.reports.order_by('-date').first()
        return latest_report.date if latest_report else self.created_at.date()
    
    @property
    def status(self):
        """Status baseado no progresso"""
        if self.is_completed:
            return 'completed'
        elif self.is_overdue:
            return 'overdue'
        else:
            return 'active'
    
    @property
    def member(self):
        """Alias para accompanied (compatibilidade com template)"""
        return self.accompanied
    
    @property
    def consolidator(self):
        """Alias para responsible (compatibilidade com template)"""
        return self.responsible

    @property
    def completed(self):
        """Mantém compatibilidade com código antigo"""
        return self.is_completed


class FollowUpStep(BaseModel):
    followup = models.ForeignKey(FollowUp, on_delete=models.CASCADE, related_name="steps")
    week = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    due_date = models.DateField()
    completed = models.BooleanField(default=False)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["week", "id"]

    def __str__(self):
        return f"{self.followup.accompanied.name} - Semana {self.week}: {self.title}"


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
