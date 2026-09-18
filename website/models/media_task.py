from django.db import models

from ._base import BaseModel

TASK_STATUS_CHOICES = [
    ('pending', 'Pendente'),
    ('in_progress', 'Em Andamento'),
    ('completed', 'Concluída'),
]


class MediaTask(BaseModel):
    title = models.CharField(max_length=200, verbose_name='Título')
    description = models.TextField(blank=True, verbose_name='Descrição')
    content = models.ForeignKey(
        'MediaContent',
        on_delete=models.CASCADE,
        related_name='tasks',
        verbose_name='Conteúdo',
    )
    assigned_to = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='media_tasks',
        verbose_name='Atribuído a',
    )
    due_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Prazo',
    )
    due_offset_days = models.IntegerField(
        null=True,
        blank=True,
        verbose_name='Prazo (dias em relação ao evento)',
        help_text='Negativo = antes do evento. Positivo = depois. Ex: -7 = 7 dias antes.',
    )
    status = models.CharField(
        max_length=20,
        choices=TASK_STATUS_CHOICES,
        default='pending',
        verbose_name='Status',
    )
    sort_order = models.PositiveSmallIntegerField(
        default=0,
        verbose_name='Ordem',
    )

    class Meta:
        verbose_name = 'Tarefa de Mídia'
        verbose_name_plural = 'Tarefas de Mídia'
        ordering = ['sort_order', 'pk']

    def __str__(self):
        return f"{self.title} ({self.content.title})"

    @property
    def due_offset_label(self):
        from website.services.media_planning import relative_days_label
        return relative_days_label(self.due_offset_days)

    def clean(self):
        super().clean()
        if self.content_id:
            from django.core.exceptions import ValidationError
            from website.services.demand_assignments import validate_ministry_assignees
            try:
                validate_ministry_assignees([self.assigned_to_id])
            except ValidationError as exc:
                raise ValidationError({'assigned_to': exc.messages})

    def save(self, *args, **kwargs):
        old = type(self).all_objects.filter(pk=self.pk).values('content_id', 'assigned_to_id').first() if self.pk else None
        if old is None or any(old[name] != getattr(self, name) for name in old):
            self.clean()
        return super().save(*args, **kwargs)
