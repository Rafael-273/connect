from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from ._base import BaseModel


class Event(BaseModel):
    title = models.CharField(max_length=255)
    description = models.TextField()
    banner = models.ImageField(upload_to='event_banners/')

    event_date = models.DateField()
    event_time = models.TimeField(null=True, blank=True)

    display_start = models.DateField()
    display_end = models.DateField()

    location = models.CharField(max_length=255, blank=True)
    link_more_info = models.URLField(blank=True, null=True)
    
    # Tipo de link
    LINK_TYPE_CHOICES = [
        ('registration', 'Inscrição'),
        ('more_info', 'Saiba Mais'),
        ('instagram', 'Instagram'),
        ('contact', 'Contato'),
        ('other', 'Outro')
    ]
    link_type = models.CharField(max_length=20, choices=LINK_TYPE_CHOICES, 
                               default='more_info', blank=True, null=True,
                               verbose_name="Tipo de Link",
                               help_text="Selecione o tipo de link para personalizar o botão")
    
    # Campos para eventos recorrentes
    is_recurring = models.BooleanField(default=False, verbose_name="Evento Recorrente", 
                                      help_text="Marque esta opção se o evento ocorre regularmente (semanal, mensal, etc.)")
    RECURRENCE_CHOICES = [
        ('weekly', 'Semanal'),
        ('biweekly', 'Quinzenal'),
        ('monthly', 'Mensal'),
        ('custom', 'Personalizado'),
    ]
    recurrence_pattern = models.CharField(max_length=20, choices=RECURRENCE_CHOICES, blank=True, null=True, 
                                         verbose_name="Padrão de Recorrência",
                                         help_text="Selecione a frequência com que o evento se repete")
    recurrence_description = models.CharField(max_length=255, blank=True, null=True, 
                                            verbose_name="Descrição da Recorrência",
                                            help_text="Descreva detalhes adicionais sobre a recorrência (ex: 'Todo domingo às 10h')")

    slug = models.SlugField(unique=True, blank=True, max_length=200)

    def __str__(self):
        return self.title

    def is_visible(self):
        from django.utils import timezone
        
        # Eventos recorrentes são sempre visíveis
        if self.is_recurring:
            return True
            
        # Para eventos normais, verifica o período de exibição
        today = timezone.now().date()
        return self.display_start <= today <= self.display_end
    
    def save(self, *args, **kwargs):
        if not self.slug:
            truncated_name = self.title[:50]
            base_slug = slugify(truncated_name)
            slug = base_slug
            num = 1

            while Event.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{num}"
                num += 1
            self.slug = slug[:50]

        super().save(*args, **kwargs)
    
    def get_weekday_name(self):
        """Retorna o nome do dia da semana para eventos recorrentes"""
        if not self.is_recurring or not self.event_date:
            return ""
        
        weekday = self.event_date.weekday()
        weekday_names = [
            'Segunda-feira',
            'Terça-feira',
            'Quarta-feira',
            'Quinta-feira',
            'Sexta-feira',
            'Sábado',
            'Domingo'
        ]
        return weekday_names[weekday]
        """Retorna o nome do dia da semana baseado na data do evento."""
        if not self.event_date:
            return ""
        
        weekday = self.event_date.weekday()
        weekday_names = [
            'Segunda-feira', 'Terça-feira', 'Quarta-feira', 
            'Quinta-feira', 'Sexta-feira', 'Sábado', 'Domingo'
        ]
        return weekday_names[weekday]
    
    def get_formatted_date(self):
        """Retorna a data formatada apropriadamente, baseada em se o evento é recorrente ou não."""
        if self.is_recurring:
            return self.get_weekday_name()
        else:
            return self.event_date.strftime("%d/%m/%Y") if self.event_date else ""


class EventDate(BaseModel):
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name='dates',
    )
    event_date = models.DateField()
    event_time = models.TimeField(null=True, blank=True)

    class Meta:
        ordering = ['event_date', 'event_time', 'created_at']

    def __str__(self):
        date_str = self.event_date.strftime("%d/%m/%Y") if self.event_date else ""
        time_str = self.event_time.strftime("%H:%M") if self.event_time else ""
        return f"{self.event.title} - {date_str} {time_str}".strip()
