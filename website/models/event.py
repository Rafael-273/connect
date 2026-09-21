from django.db import models, transaction
from django.utils import timezone
from django.utils.text import slugify
from ._base import BaseModel


class Event(BaseModel):
    INSTITUTIONAL_STATUS_PENDING = 'pending'
    INSTITUTIONAL_STATUS_PUBLISHED = 'published'
    INSTITUTIONAL_STATUS_CHOICES = [
        (INSTITUTIONAL_STATUS_PENDING, 'Pendente de publicação institucional'),
        (INSTITUTIONAL_STATUS_PUBLISHED, 'Publicado institucionalmente'),
    ]

    title = models.CharField(max_length=255)
    description = models.TextField()
    banner = models.ImageField(upload_to='event_banners/')

    event_date = models.DateField()
    event_time = models.TimeField(null=True, blank=True)

    end_date = models.DateField(null=True, blank=True, verbose_name='Data de término')
    end_time = models.TimeField(null=True, blank=True, verbose_name='Horário de término')

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

    event_type = models.ForeignKey(
        'MediaEventType',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='events',
        verbose_name='Tipo de evento (Mídia)',
    )
    # Publication and media planning have independent lifecycles.
    institutional_status = models.CharField(
        max_length=16,
        choices=INSTITUTIONAL_STATUS_CHOICES,
        default=INSTITUTIONAL_STATUS_PUBLISHED,
        verbose_name='Status institucional',
    )
    institutional_published_at = models.DateTimeField(null=True, blank=True)

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
    
    @transaction.atomic
    def save(self, *args, **kwargs):
        previous = type(self).all_objects.select_for_update().filter(pk=self.pk).values('event_date').first() if self.pk else None
        self.event_date = self._meta.get_field('event_date').to_python(self.event_date)
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
        fields = kwargs.get('update_fields')
        if previous and previous['event_date'] != self.event_date and (fields is None or 'event_date' in fields):
            from website.services.media_planning import recalculate_event_dates
            recalculate_event_dates(self)
    
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

    @property
    def is_media_organized(self):
        return getattr(getattr(self, 'media_organization', None), 'status', None) == MediaEventOrganization.STATUS_ORGANIZED


class MediaEventOrganization(BaseModel):
    """Operational media state for a non-recurring central Event."""

    STATUS_PENDING = 'pending'
    STATUS_ORGANIZED = 'organized'
    STATUS_REMOVED = 'removed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pendente de organização'),
        (STATUS_ORGANIZED, 'Organizado'),
        (STATUS_REMOVED, 'Removido da mídia'),
    ]

    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name='media_organization')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_from_media = models.BooleanField(default=False)
    organized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Organização de mídia do evento'
        verbose_name_plural = 'Organizações de mídia dos eventos'

    def __str__(self):
        return f'Mídia: {self.event.title}'


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
