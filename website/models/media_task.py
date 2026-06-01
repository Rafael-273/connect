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
    status = models.CharField(
        max_length=20,
        choices=TASK_STATUS_CHOICES,
        default='pending',
        verbose_name='Status',
    )

    class Meta:
        verbose_name = 'Tarefa de Mídia'
        verbose_name_plural = 'Tarefas de Mídia'
        ordering = ['due_date', 'created_at']

    def __str__(self):
        return f"{self.title} ({self.content.title})"
