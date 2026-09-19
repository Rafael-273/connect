from django.db import models, transaction

from ._base import BaseModel

CONTENT_TYPE_CHOICES = [
    ('artwork', 'Arte'),
    ('reel', 'Reel'),
    ('carousel', 'Carrossel'),
    ('story', 'Story'),
    ('photography', 'Fotografia'),
    ('video', 'Vídeo'),
    ('livestream', 'Transmissão ao Vivo'),
    ('youtube', 'YouTube'),
    ('whatsapp', 'WhatsApp'),
    ('website_banner', 'Banner do Site'),
    ('devotional', 'Devocional'),
    ('announcement', 'Comunicado'),
    ('coordination', 'Organização'),
    ('contact', 'Contato'),
    ('approval', 'Aprovação'),
]

STATUS_CHOICES = [
    ('pending', 'Pendente'),
    ('in_progress', 'Em Produção'),
    ('review', 'Em Revisão'),
    ('approved', 'Aprovado'),
    ('scheduled', 'Agendado'),
    ('published', 'Publicado'),
]

PRIORITY_CHOICES = [
    ('low', 'Baixa'),
    ('medium', 'Média'),
    ('high', 'Alta'),
    ('urgent', 'Urgente'),
]

PUBLICATION_CHANNEL_CHOICES = [
    ('instagram', 'Instagram'),
    ('instagram_stories', 'Instagram Stories'),
    ('facebook', 'Facebook'),
    ('youtube', 'YouTube'),
    ('whatsapp', 'WhatsApp'),
    ('website', 'Site'),
    ('tiktok', 'TikTok'),
    ('other', 'Outro'),
]


class MediaContent(BaseModel):
    title = models.CharField(max_length=200, verbose_name='Título')
    description = models.TextField(blank=True, verbose_name='Descrição')
    content_type = models.CharField(
        max_length=20,
        choices=CONTENT_TYPE_CHOICES,
        verbose_name='Tipo de Conteúdo',
    )
    event = models.ForeignKey(
        'Event',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='media_contents',
        verbose_name='Evento',
    )
    publication_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Data de Publicação',
    )
    due_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Prazo de Entrega',
    )
    start_date = models.DateTimeField(null=True, blank=True, verbose_name='Início previsto')
    # Frozen rules: future template edits never rewrite an existing demand.
    start_offset_days = models.IntegerField(null=True, blank=True, editable=False)
    due_offset_days = models.IntegerField(null=True, blank=True, editable=False)
    publication_offset_days = models.IntegerField(null=True, blank=True, editable=False)
    start_date_auto = models.BooleanField(default=False, editable=False)
    due_date_auto = models.BooleanField(default=False, editable=False)
    publication_date_auto = models.BooleanField(default=False, editable=False)

    responsible = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='media_contents_responsible',
        verbose_name='Responsável',
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='Status',
    )
    priority = models.CharField(
        max_length=10,
        choices=PRIORITY_CHOICES,
        default='medium',
        verbose_name='Prioridade',
    )
    month_plan = models.ForeignKey(
        'MediaMonthPlan',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='contents',
        verbose_name='Plano Mensal',
    )
    category = models.ForeignKey(
        'MediaContentCategory',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='contents',
        verbose_name='Categoria',
    )
    sub_team = models.ForeignKey(
        'MediaSubTeam',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='contents',
        verbose_name='Equipe responsável',
    )
    assigned_role = models.ForeignKey(
        'MediaRole',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='contents',
        verbose_name='Função responsável',
    )
    requires_recording = models.BooleanField(default=False, verbose_name='Necessita gravação?')
    requires_editing = models.BooleanField(default=False, verbose_name='Necessita edição?')
    publication_channel = models.CharField(
        max_length=30,
        choices=PUBLICATION_CHANNEL_CHOICES,
        blank=True,
        verbose_name='Canal de publicação',
    )
    template_item = models.ForeignKey(
        'MediaPlanningTemplateItem',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='generated_contents',
        verbose_name='Item de template de origem',
    )
    observations = models.TextField(blank=True, verbose_name='Observações')

    class Meta:
        verbose_name = 'Conteúdo de Mídia'
        verbose_name_plural = 'Conteúdos de Mídia'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} ({self.get_content_type_display()})"

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError
        from website.services.demand_assignments import validate_ministry_assignees
        try:
            user_ids = [self.responsible_id]
            if hasattr(self, '_replacement_assignee_ids'):
                user_ids += self._replacement_assignee_ids
            elif self.pk:
                user_ids += list(self.tasks.values_list('assigned_to_id', flat=True))
            validate_ministry_assignees(user_ids)
        except ValidationError as exc:
            raise ValidationError({'responsible': exc.messages})
        if self.assigned_role_id and self.assigned_role.sub_team_id:
            if self.assigned_role.sub_team_id != self.sub_team_id:
                raise ValidationError({'assigned_role': 'A função deve pertencer à equipe selecionada.'})

    @transaction.atomic
    def save(self, *args, **kwargs):
        # Existing assignments survive departures; validate new or changed assignment data.
        old = type(self).all_objects.filter(pk=self.pk).values('sub_team_id', 'responsible_id', 'assigned_role_id').first() if self.pk else None
        if old is None or any(old[name] != getattr(self, name) for name in old):
            self.clean()
        fields = kwargs.get('update_fields')
        if self.pk:
            previous = type(self).all_objects.select_for_update().get(pk=self.pk)
            for date_field in ('start_date', 'due_date', 'publication_date'):
                flag = date_field + '_auto'
                date_changed = fields is None or date_field in fields
                event_changed = (fields is None or 'event' in fields or 'event_id' in fields) and self.event_id != previous.event_id
                value = self._meta.get_field(date_field).to_python(getattr(self, date_field))
                if event_changed or (date_changed and value != getattr(previous, date_field)):
                    setattr(self, flag, False)
                else:
                    setattr(self, flag, getattr(previous, flag))
                if fields is not None:
                    fields = set(fields) | {flag}
            if fields is not None:
                kwargs['update_fields'] = fields
        return super().save(*args, **kwargs)
