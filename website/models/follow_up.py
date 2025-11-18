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
    week = models.PositiveIntegerField(help_text="Número da semana")
    title = models.CharField(max_length=200, help_text="Título/tema da semana")
    description = models.TextField(blank=True, null=True, help_text="Dicas e orientações para esta semana")

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
    current_week = models.PositiveIntegerField(default=1, help_text="Semana atual")
    profile_notes = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.accompanied.name} - {self.responsible.name}"

    def save(self, *args, **kwargs):
        if not self.start_date:
            self.start_date = timezone.now().date()
            
        super().save(*args, **kwargs)

    @property
    def current_step(self):
        """Retorna o step/dicas da semana atual"""
        if self.template:
            return self.template.steps.filter(week=self.current_week).first()
        return None

    @property
    def total_weeks(self):
        """Total de semanas no template"""
        if self.template:
            return self.template.steps.count()
        return 0

    @property
    def progress_percentage(self):
        """Progresso baseado em relatórios enviados"""
        total = self.total_weeks
        if total == 0:
            return 0
        reports_count = self.reports.count()
        return min(int((reports_count / total) * 100), 100)

    @property
    def last_contact(self):
        """Data do último relatório/contato"""
        latest_report = self.reports.order_by('-date').first()
        return latest_report.date if latest_report else self.created_at.date()
    
    @property
    def status(self):
        """Status baseado nos relatórios"""
        if not self.is_active:
            return 'completed'
        
        latest_report = self.reports.order_by('-date').first()
        if not latest_report:
            return 'pending'
        
        # Verifica se está atrasado (mais de 2 semanas sem relatório)
        days_since_last = (timezone.now().date() - latest_report.date).days
        if days_since_last > 14:
            return 'overdue'
        
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
        return not self.is_active
    
    @property
    def is_completed(self):
        return not self.is_active
    
    @property
    def is_overdue(self):
        """Verifica se está atrasado"""
        return self.status == 'overdue'
    
    # Propriedades para compatibilidade com código existente
    @property
    def completed_steps(self):
        return self.reports.count()
    
    @property
    def total_steps(self):
        return self.total_weeks
    
    # Aliases para manter compatibilidade
    @property
    def current_period(self):
        return self.current_week
    
    @property
    def total_periods(self):
        return self.total_weeks


class FollowUpReport(BaseModel):
    STATUS_CHOICES = [
        ('excellent', 'Excelente progresso'),
        ('good', 'Bom progresso'),
        ('regular', 'Progresso regular'),
        ('needs_support', 'Precisa de apoio'),
        ('difficult', 'Enfrentando dificuldades'),
        ('no_contact', 'Sem contato'),
    ]

    FOLLOWUP_TYPE_CHOICES = [
        ('consolidation', 'Consolidação'),
        ('discipleship', 'Discipulado'),
    ]

    followup = models.ForeignKey('FollowUp', on_delete=models.CASCADE, related_name='reports')
    week = models.PositiveIntegerField(default=1, help_text="Semana referente a este relatório")
    type = models.CharField(max_length=20, choices=FOLLOWUP_TYPE_CHOICES, default='consolidation')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES)
    location = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(help_text="Como foi o andamento nesta semana?")
    prayer_request = models.TextField(blank=True, null=True)
    date = models.DateField(auto_now_add=True)

    attended_service = models.BooleanField(default=False, verbose_name="Participou do culto?")
    reading_bible = models.BooleanField(default=False, verbose_name="Está lendo a Bíblia?")
    praying_regularly = models.BooleanField(default=False, verbose_name="Está orando regularmente?")
    
    has_spiritual_life = models.BooleanField(default=False, verbose_name="Demonstra vida do Espírito Santo?", help_text="Frutos do Espírito visíveis: amor, alegria, paz, paciência, bondade, etc.")
    building_relationships = models.BooleanField(default=False, verbose_name="Está criando vínculos com outros membros?")
    
    lifestyle_changes = models.BooleanField(default=False, verbose_name="Demonstra mudanças no estilo de vida?")
    overcoming_struggles = models.BooleanField(default=False, verbose_name="Vencendo lutas/vícios anteriores?")
    
    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"{self.followup.accompanied.name} - Semana {self.week} - {self.get_status_display()} ({self.date})"
    
    @property
    def spiritual_health_score(self):
        """Calcula um score de saúde espiritual (0-100)"""
        total_indicators = 7
        positive_count = sum([
            self.attended_service,
            self.reading_bible,
            self.praying_regularly,
            self.has_spiritual_life,
            self.building_relationships,
            self.lifestyle_changes,
            self.overcoming_struggles,
        ])
        return int((positive_count / total_indicators) * 100)
    
    # Aliases para compatibilidade
    @property
    def period(self):
        return self.week
    
    @property
    def period_name(self):
        return "Semana"
    
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        
        # Atualizar a semana atual do followup se este relatório for mais recente
        if self.week >= self.followup.current_week:
            self.followup.current_week = self.week + 1
            self.followup.save(update_fields=['current_week'])
