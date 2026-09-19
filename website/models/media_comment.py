from django.core.exceptions import ValidationError
from django.db import models

from ._base import BaseModel


class MediaComment(BaseModel):
    text = models.TextField(verbose_name='Comentário')
    author = models.ForeignKey(
        'User',
        on_delete=models.CASCADE,
        related_name='media_comments',
        verbose_name='Autor',
    )
    content = models.ForeignKey(
        'MediaContent',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='comments',
        verbose_name='Conteúdo',
    )
    task = models.ForeignKey(
        'MediaTask',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='comments',
        verbose_name='Tarefa',
    )

    class Meta:
        verbose_name = 'Comentário de Mídia'
        verbose_name_plural = 'Comentários de Mídia'
        ordering = ['created_at']

    def clean(self):
        if not self.content_id and not self.task_id:
            raise ValidationError(
                'O comentário deve estar vinculado a um conteúdo ou a uma tarefa.'
            )
        if self.content_id and self.task_id:
            raise ValidationError(
                'O comentário não pode estar vinculado a um conteúdo e a uma tarefa ao mesmo tempo.'
            )

    def __str__(self):
        target = self.content or self.task
        return f"Comentário de {self.author} em {target}"
