from django.db import models
from ._base import BaseModel


class Ministry(BaseModel):
    name = models.CharField(max_length=100, unique=True)
    code = models.SlugField(
        max_length=100,
        unique=True,
        blank=True,
        null=True,
        help_text='Código estável usado por permissões internas (ex: midia_externa).',
    )
    description = models.TextField(blank=True, null=True)
    color = models.CharField(
        max_length=7,
        default='#F97316',
        help_text='Cor do ministério em hexadecimal (ex: #F97316)'
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name='Ministério Ativo'
    )

    class Meta:
        verbose_name = 'Ministério'
        verbose_name_plural = 'Ministérios'
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_leaders(self):
        """Retorna os líderes do ministério"""
        from .ministry_membership import MinistryMembership
        return MinistryMembership.objects.filter(
            ministry=self,
            role='leader',
            is_active=True
        ).select_related('member')

    def get_members(self):
        """Retorna todos os membros ativos (incluindo líderes)"""
        from .ministry_membership import MinistryMembership
        return MinistryMembership.objects.filter(
            ministry=self,
            is_active=True
        ).select_related('member')

    def get_member_count(self):
        """Retorna o número total de membros ativos"""
        return self.get_members().count()

    def get_leader_count(self):
        """Retorna o número de líderes"""
        return self.get_leaders().count()
