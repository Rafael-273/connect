from django.db import models
from django.core.exceptions import ValidationError
from ._base import BaseModel
from .member import Member
from .ministry import Ministry


class Schedule(BaseModel):
    """Escala de membro em ministério para uma data específica."""

    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name='schedules',
        verbose_name='Membro',
    )
    ministry = models.ForeignKey(
        Ministry,
        on_delete=models.CASCADE,
        related_name='schedules',
        verbose_name='Ministério',
    )
    scheduled_date = models.DateField(verbose_name='Data da Escala')
    notes = models.TextField(blank=True, null=True, verbose_name='Observações')

    override_conflict = models.BooleanField(
        default=False,
        verbose_name='Conflito ignorado',
        help_text='Indica que um conflito foi detectado e o usuário optou por prosseguir.',
    )
    override_by = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='schedule_overrides',
        verbose_name='Override por',
    )

    class Meta:
        indexes = [
            models.Index(fields=['member', 'scheduled_date'], name='idx_member_date'),
        ]
        ordering = ['-scheduled_date', 'member']
        verbose_name = 'Escala'
        verbose_name_plural = 'Escalas'

    def __str__(self):
        return f'{self.member.name} - {self.ministry.name} ({self.scheduled_date})'

    def get_conflicts(self):
        """Retorna escalas conflitantes (mesmo membro, mesma data, outro ministério)."""
        qs = Schedule.objects.filter(
            member=self.member,
            scheduled_date=self.scheduled_date,
        ).exclude(ministry=self.ministry)
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        return qs

    def clean(self):
        super().clean()
        conflicts = self.get_conflicts()
        if conflicts.exists() and not self.override_conflict:
            ministries = ', '.join(c.ministry.name for c in conflicts)
            raise ValidationError(
                f'{self.member.name} já está escalado(a) em: {ministries} '
                f'no dia {self.scheduled_date.strftime("%d/%m/%Y")}. '
                f'Confirme o override para prosseguir.'
            )
