from django.core.exceptions import ValidationError
from django.db import models
from django.utils.html import strip_tags
import uuid

from ._base import BaseModel
from website.utils.manual_content import sanitize_manual_content


class MinistryManual(BaseModel):
    title = models.CharField('Título', max_length=200)
    summary = models.TextField('Resumo', blank=True)
    content = models.TextField('Conteúdo')
    ministry = models.ForeignKey('Ministry', on_delete=models.CASCADE, related_name='manuals')
    sub_team = models.ForeignKey(
        'MediaSubTeam', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='manuals', verbose_name='Equipe',
    )
    created_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True)
    is_active = models.BooleanField('Ativo', default=True)
    is_draft = models.BooleanField('Rascunho', default=False)
    origin = models.CharField('Origem', max_length=12, default='manual', choices=[('manual', 'Manual'), ('ai', 'IA')])

    class Meta:
        ordering = ['title']
        verbose_name = 'Manual do Ministério'
        verbose_name_plural = 'Manuais dos Ministérios'

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if self.sub_team_id and self.sub_team.ministry_id != self.ministry_id:
            raise ValidationError({'sub_team': 'A equipe deve pertencer ao ministério do manual.'})
        rendered = sanitize_manual_content(self.content)
        if not strip_tags(rendered).replace('&nbsp;', '').strip():
            raise ValidationError({'content': 'Escreva o conteúdo do manual.'})

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    @property
    def safe_content(self):
        return sanitize_manual_content(self.content)


class ManualGenerationJob(models.Model):
    ministry = models.ForeignKey('Ministry', on_delete=models.CASCADE)
    created_by = models.ForeignKey('User', on_delete=models.CASCADE)
    audio = models.BinaryField(null=True, editable=False)
    filename = models.CharField(max_length=255)
    transcript = models.TextField(blank=True)
    status = models.CharField(max_length=20, default='waiting')
    run_token = models.UUIDField(default=uuid.uuid4, editable=False)
    error = models.CharField(max_length=300, blank=True)
    manual = models.ForeignKey(MinistryManual, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
