"""Media team defaults. These records never update existing schedule assignments."""
import calendar
import datetime
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from simple_history.models import HistoricalRecords

from ._base import BaseModel


class MediaTeamActivity(BaseModel):
    history = HistoricalRecords()
    sub_team = models.ForeignKey('MediaSubTeam', on_delete=models.PROTECT, related_name='activities')
    name = models.CharField('Atividade', max_length=100)
    description = models.TextField('Descrição', blank=True)
    sort_order = models.PositiveIntegerField('Ordem', default=0)
    is_active = models.BooleanField('Ativa', default=True)

    class Meta:
        ordering = ['sort_order', 'name', 'pk']
        verbose_name = 'Atividade da equipe de mídia'
        constraints = [models.UniqueConstraint(fields=['sub_team', 'name'], name='media_team_activity_name_unique')]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        validate_media_team(self.sub_team)
        self.name = self.name.strip()
        if not self.name:
            raise ValidationError({'name': 'Informe a atividade.'})
        if self.pk and type(self).all_objects.filter(pk=self.pk).exclude(sub_team_id=self.sub_team_id).exists():
            raise ValidationError({'sub_team': 'A equipe de uma atividade não pode ser alterada.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


def validate_media_team(team):
    from website.services.ministry_organization import media_ministry
    if team.ministry != media_ministry():
        raise ValidationError('Configuração exclusiva das equipes da Mídia.')


class MediaTeamService(BaseModel):
    history = HistoricalRecords()
    REGULAR_SERVICE_CHOICES = (
        ('sunday_09', 'Domingo · 09:00'),
        ('sunday_19', 'Domingo · 19:00'),
        ('wednesday_20', 'Quarta-feira · 20:00'),
    )
    REGULAR_SERVICE_DETAILS = {
        'sunday_09': (6, datetime.time(9, 0)),
        'sunday_19': (6, datetime.time(19, 0)),
        'wednesday_20': (2, datetime.time(20, 0)),
    }
    sub_team = models.ForeignKey('MediaSubTeam', on_delete=models.PROTECT, related_name='service_defaults')
    # Legacy agenda links remain valid, but new permanent services use regular_service.
    event = models.ForeignKey('Event', on_delete=models.CASCADE, null=True, blank=True, related_name='media_team_services', verbose_name='Culto / evento')
    event_date = models.ForeignKey('EventDate', on_delete=models.CASCADE, null=True, blank=True, related_name='media_team_services', verbose_name='Data adicional')
    regular_service = models.CharField('Culto regular', max_length=20, choices=REGULAR_SERVICE_CHOICES, blank=True)
    member_count = models.PositiveSmallIntegerField('Quantidade de integrantes', default=2, validators=[MinValueValidator(1)])
    is_active = models.BooleanField('Ativa', default=True)

    class Meta:
        ordering = ['regular_service', 'event__title', 'pk']
        verbose_name = 'Atuação da equipe de mídia'
        constraints = [
            models.CheckConstraint(check=models.Q(member_count__gte=1), name='media_service_positive_members'),
            models.UniqueConstraint(fields=['sub_team', 'event'], condition=models.Q(event_date__isnull=True, deleted__isnull=True), name='media_service_primary_unique'),
            models.UniqueConstraint(fields=['sub_team', 'event_date'], condition=models.Q(event_date__isnull=False, deleted__isnull=True), name='media_service_extra_unique'),
            models.UniqueConstraint(fields=['sub_team', 'regular_service'], condition=models.Q(regular_service__gt='', deleted__isnull=True), name='media_service_regular_unique'),
        ]

    @property
    def activities(self):
        # M2M joins do not apply the soft-delete manager of a through model.
        return MediaTeamActivity.objects.filter(
            service_links__service=self, service_links__deleted__isnull=True,
        )

    @property
    def source_available(self):
        if self.regular_service:
            return True
        return not self.event.deleted and not (self.event_date_id and self.event_date.deleted) and bool(self.service_time)

    @property
    def service_time(self):
        if self.regular_service:
            return self.REGULAR_SERVICE_DETAILS[self.regular_service][1]
        return self.event_date.event_time if self.event_date_id else self.event.event_time

    @property
    def service_label(self):
        if self.regular_service:
            return self.get_regular_service_display()
        source = self.event_date if self.event_date_id else self.event
        if self.event.is_recurring and not self.event_date_id:
            date_label = f'{self.event.get_recurrence_pattern_display()} · {self.event.get_weekday_name()}'
        else:
            date_label = source.event_date.strftime('%d/%m/%Y')
        hour = self.service_time.strftime('%H:%M') if self.service_time else 'sem horário'
        return f'{self.event.title} · {date_label} · {hour}'

    def __str__(self):
        return f'{self.sub_team.name} — {self.service_label}'

    @classmethod
    def regular_dates(cls, key, period):
        """All dates for a fixed weekly regular service in a planning month."""
        weekday, _time = cls.REGULAR_SERVICE_DETAILS[key]
        current = period.replace(day=1)
        last = current.replace(day=calendar.monthrange(period.year, period.month)[1])
        current += datetime.timedelta(days=(weekday - current.weekday()) % 7)
        while current <= last:
            yield current
            current += datetime.timedelta(days=7)

    def clean(self):
        super().clean()
        validate_media_team(self.sub_team)
        if self.regular_service:
            if self.event_id or self.event_date_id:
                raise ValidationError('Uma atuação regular não pode apontar para um evento da agenda.')
            return
        if not self.event_id:
            raise ValidationError('Selecione um culto regular.')
        if self.event_date_id and self.event_date.event_id != self.event_id:
            raise ValidationError({'event_date': 'A data deve pertencer ao evento selecionado.'})
        if self.is_active and (self.event.deleted or (self.event_date_id and self.event_date.deleted)):
            raise ValidationError('Selecione um evento ativo da agenda.')
        if self.is_active and not self.service_time:
            raise ValidationError({'event': 'O culto precisa ter horário cadastrado na agenda.'})
        if self.pk and type(self).all_objects.filter(pk=self.pk).exclude(sub_team_id=self.sub_team_id).exists():
            raise ValidationError({'sub_team': 'A equipe de uma atuação não pode ser alterada.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def snapshot(self):
        """Copy values into a future schedule snapshot, never use live defaults as history."""
        return {
            'service_id': self.pk, 'team_id': self.sub_team_id, 'team_name': self.sub_team.name,
            'event_id': self.event_id, 'event_date_id': self.event_date_id,
            'regular_service': self.regular_service,
            'event_title': self.event.title if self.event_id else self.service_label,
            'date': (self.event_date.event_date if self.event_date_id else self.event.event_date).isoformat() if self.event_id else None,
            'time': self.service_time.isoformat() if self.service_time else None,
            'recurring': bool(self.regular_service) or (self.event.is_recurring and not self.event_date_id),
            'recurrence_pattern': 'weekly' if self.regular_service else (self.event.recurrence_pattern if not self.event_date_id else None),
            'label': self.service_label, 'member_count': self.member_count,
            'activities': [
                {'id': link.activity_id, 'name': link.activity.name, 'order': link.activity.sort_order}
                for link in self.activity_links.filter(activity__is_active=True, activity__deleted__isnull=True)
                .select_related('activity').order_by('activity__sort_order', 'activity__name', 'pk')
            ],
        }


class MediaTeamServiceActivity(BaseModel):
    history = HistoricalRecords()
    service = models.ForeignKey(MediaTeamService, on_delete=models.CASCADE, related_name='activity_links')
    activity = models.ForeignKey(MediaTeamActivity, on_delete=models.PROTECT, related_name='service_links')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['service', 'activity'], condition=models.Q(deleted__isnull=True), name='media_service_activity_unique')]

    def clean(self):
        super().clean()
        if self.service.sub_team_id != self.activity.sub_team_id:
            raise ValidationError('A atividade deve pertencer à equipe da atuação.')

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
