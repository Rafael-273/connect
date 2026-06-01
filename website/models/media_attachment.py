import os

from django.core.exceptions import ValidationError
from django.db import models

from ._base import BaseModel

ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'pdf', 'psd', 'ai', 'mp4'}


def validate_media_file(value):
    ext = os.path.splitext(value.name)[1].lstrip('.').lower()
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ', '.join(sorted(ALLOWED_EXTENSIONS))
        raise ValidationError(f'Extensão não permitida. Tipos aceitos: {allowed}')


class MediaAttachment(BaseModel):
    name = models.CharField(max_length=200, verbose_name='Nome')
    file = models.FileField(
        upload_to='media_planning/',
        validators=[validate_media_file],
        verbose_name='Arquivo',
    )
    content = models.ForeignKey(
        'MediaContent',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='attachments',
        verbose_name='Conteúdo',
    )
    task = models.ForeignKey(
        'MediaTask',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='attachments',
        verbose_name='Tarefa',
    )
    uploaded_by = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='media_attachments',
        verbose_name='Enviado por',
    )

    class Meta:
        verbose_name = 'Anexo de Mídia'
        verbose_name_plural = 'Anexos de Mídia'
        ordering = ['-created_at']

    def clean(self):
        if not self.content_id and not self.task_id:
            raise ValidationError(
                'O anexo deve estar vinculado a um conteúdo ou a uma tarefa.'
            )
        if self.content_id and self.task_id:
            raise ValidationError(
                'O anexo não pode estar vinculado a um conteúdo e a uma tarefa ao mesmo tempo.'
            )

    def __str__(self):
        return self.name
