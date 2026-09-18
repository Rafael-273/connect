from django.core.exceptions import ValidationError
from django.db import models
from django.utils.html import strip_tags

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
