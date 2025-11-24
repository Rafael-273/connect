from django.db import models
from datetime import datetime, timedelta
from ._base import BaseModel


class MinistrationSchedule(BaseModel):
    """Modelo para escala de ministração semanal"""
    
    week_start_date = models.DateField(
        verbose_name='Início da Semana',
        help_text='Data de início da semana (geralmente segunda-feira)',
        unique=True
    )
    members = models.ManyToManyField(
        'Member',
        related_name='ministration_schedules',
        verbose_name='Ministradores Escalados',
        limit_choices_to={'ministry__name__icontains': 'ministração'}
    )
    leaders = models.ManyToManyField(
        'Member',
        related_name='ministration_schedules_as_leader',
        verbose_name='Líderes da Escala',
        blank=True,
        limit_choices_to={'ministry__name__icontains': 'ministração'}
    )
    notes = models.TextField(
        blank=True,
        null=True,
        verbose_name='Observações'
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name='Ativa'
    )
    
    class Meta:
        verbose_name = 'Escala de Ministração'
        verbose_name_plural = 'Escalas de Ministração'
        ordering = ['-week_start_date']
    
    def __str__(self):
        return f"Escala {self.week_start_date.strftime('%d/%m/%Y')}"
    
    def get_wednesday_date(self):
        """Retorna a data da quarta-feira desta semana"""
        days_until_wednesday = (2 - self.week_start_date.weekday()) % 7
        return self.week_start_date + timedelta(days=days_until_wednesday)
    
    def get_sunday_date(self):
        """Retorna a data do domingo desta semana"""
        days_until_sunday = (6 - self.week_start_date.weekday()) % 7
        if days_until_sunday == 0 and self.week_start_date.weekday() != 6:
            days_until_sunday = 7
        return self.week_start_date + timedelta(days=days_until_sunday)


class WordOfKnowledge(BaseModel):
    """Modelo para armazenar palavras de conhecimento"""
    
    SERVICE_CHOICES = [
        ('wednesday', 'Quarta-feira'),
        ('sunday', 'Domingo'),
    ]
    
    member = models.ForeignKey(
        'Member',
        on_delete=models.CASCADE,
        related_name='words_of_knowledge',
        verbose_name='Membro'
    )
    word = models.TextField(verbose_name='Palavra de Conhecimento')
    service_type = models.CharField(
        max_length=20,
        choices=SERVICE_CHOICES,
        verbose_name='Tipo de Culto',
        editable=False
    )
    service_date = models.DateField(verbose_name='Data do Culto', editable=False)
    recorded_at = models.DateTimeField(auto_now_add=True, verbose_name='Registrado em')
    resulted_in_healing = models.BooleanField(
        default=False,
        verbose_name='Resultou em Cura'
    )
    
    # Campos de aprovação
    is_approved = models.BooleanField(
        default=False,
        verbose_name='Aprovada',
        help_text='Palavra aprovada para o culto'
    )
    approved_by = models.ForeignKey(
        'Member',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_words',
        verbose_name='Aprovada por'
    )
    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Aprovada em'
    )
    
    class Meta:
        verbose_name = 'Palavra de Conhecimento'
        verbose_name_plural = 'Palavras de Conhecimento'
        ordering = ['-service_date', '-recorded_at']
    
    def save(self, *args, **kwargs):
        """Calcula automaticamente o tipo e data do culto baseado na data de registro"""
        if not self.pk:  # Apenas na criação
            recorded_date = self.recorded_at if self.recorded_at else datetime.now()
            weekday = recorded_date.weekday()  # 0=Monday, 6=Sunday
            
            # Lógica: 
            # Domingo (6) até Quarta (2) = próximo culto é Quarta
            # Quinta (3) até Sábado (5) = próximo culto é Domingo
            
            if weekday in [6, 0, 1, 2]:  # Domingo, Segunda, Terça, Quarta
                # Próximo culto é Quarta
                self.service_type = 'wednesday'
                # Calcular próxima quarta
                days_until_wednesday = (2 - weekday) % 7
                if days_until_wednesday == 0 and recorded_date.hour >= 20:  # Se é quarta após culto
                    days_until_wednesday = 7
                self.service_date = (recorded_date + timedelta(days=days_until_wednesday)).date()
            else:  # Quinta, Sexta, Sábado
                # Próximo culto é Domingo
                self.service_type = 'sunday'
                # Calcular próximo domingo
                days_until_sunday = (6 - weekday) % 7
                if days_until_sunday == 0 and recorded_date.hour >= 19:  # Se é domingo após culto
                    days_until_sunday = 7
                self.service_date = (recorded_date + timedelta(days=days_until_sunday)).date()
            
            # Auto-aprovar se o membro for aprovador
            if self.member.is_approver:
                self.is_approved = True
                self.approved_by = self.member
                self.approved_at = datetime.now()
        
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.member.name} - {self.get_service_type_display()} - {self.service_date}"


class Healing(BaseModel):
    """Modelo para armazenar curas"""
    
    HEALING_TYPE_CHOICES = [
        ('total', 'Cura Total'),
        ('partial', 'Cura Parcial'),
    ]
    
    member = models.ForeignKey(
        'Member',
        on_delete=models.CASCADE,
        related_name='healings_recorded',
        verbose_name='Membro que Registrou'
    )
    description = models.TextField(verbose_name='Descrição da Cura')
    healed_person_name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name='Nome da Pessoa Curada'
    )
    body_part = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name='Parte do Corpo',
        help_text='Ex: perna, olho, cabeça'
    )
    condition = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name='Condição',
        help_text='Ex: perna quebrada, cegueira, enxaqueca'
    )
    healing_type = models.CharField(
        max_length=10,
        choices=HEALING_TYPE_CHOICES,
        default='total',
        verbose_name='Tipo de Cura'
    )
    word_of_knowledge = models.ForeignKey(
        'WordOfKnowledge',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='healings',
        verbose_name='Palavra de Conhecimento Relacionada'
    )
    healing_date = models.DateField(verbose_name='Data da Cura')
    recorded_at = models.DateTimeField(auto_now_add=True, verbose_name='Registrado em')
    
    class Meta:
        verbose_name = 'Cura'
        verbose_name_plural = 'Curas'
        ordering = ['-healing_date', '-recorded_at']
    
    def __str__(self):
        return f"Cura registrada por {self.member.name} - {self.healing_date}"
