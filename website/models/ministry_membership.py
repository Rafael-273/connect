from django.db import models
from ._base import BaseModel
from .ministry import Ministry
from .member import Member


class MinistryMembership(BaseModel):
    """
    Modelo intermediário para relacionar membros e ministérios com papéis específicos.
    Permite que um membro seja líder de um ministério e membro de outros.
    """
    ROLE_CHOICES = [
        ('leader', 'Líder'),
        ('member', 'Membro'),
    ]

    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name='ministry_memberships'
    )
    ministry = models.ForeignKey(
        Ministry,
        on_delete=models.CASCADE,
        related_name='memberships'
    )
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default='member',
        verbose_name='Papel no Ministério'
    )
    joined_date = models.DateField(
        auto_now_add=True,
        verbose_name='Data de Entrada'
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name='Ativo no Ministério'
    )

    class Meta:
        unique_together = ('member', 'ministry')
        verbose_name = 'Membro do Ministério'
        verbose_name_plural = 'Membros dos Ministérios'
        ordering = ['-role', 'member__name']

    def __str__(self):
        return f"{self.member.name} - {self.ministry.name} ({self.get_role_display()})"
