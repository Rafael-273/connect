"""Planning extensions of the canonical ministry schedule, before member assignment."""
from django.db import models
from simple_history.models import HistoricalRecords

from ._base import BaseModel


class ScheduleAssistantState(BaseModel):
    history = HistoricalRecords()
    schedule = models.OneToOneField('MonthlySchedule', on_delete=models.CASCADE, related_name='assistant_state')
    ministry = models.ForeignKey('Ministry', on_delete=models.CASCADE)
    period = models.DateField()
    status = models.CharField(max_length=12, default='draft', choices=[
        ('draft', 'Rascunho'), ('published', 'Publicada'), ('discarded', 'Descartado'),
    ])
    revision = models.PositiveIntegerField(default=1)
    current_step = models.PositiveSmallIntegerField(default=1)
    # Last declaration revision; planning edits never clear availability records.
    availability_revision = models.PositiveIntegerField(null=True, blank=True)
    availability_review = models.JSONField(default=list, blank=True)
    generation_signature = models.CharField(max_length=64, blank=True)
    generation_report = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, related_name='+')
    updated_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, related_name='+')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['ministry', 'period'], condition=models.Q(status='draft', deleted__isnull=True), name='one_media_draft_per_period'),
            models.CheckConstraint(check=models.Q(current_step__gte=1, current_step__lte=3), name='schedule_assistant_valid_step'),
        ]


class ScheduleOccurrence(BaseModel):
    history = HistoricalRecords()
    schedule_day = models.ForeignKey('ScheduleDay', on_delete=models.CASCADE, related_name='occurrences')
    source_event = models.ForeignKey('Event', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    source_event_date = models.ForeignKey('EventDate', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    # Stable import identity survives local changes and removal of the agenda source.
    source_key = models.CharField(max_length=160)
    source_label = models.CharField(max_length=300, blank=True)
    name = models.CharField(max_length=255)
    start_time = models.TimeField()
    notes = models.TextField(blank=True)
    availability_review_required = models.BooleanField(default=False)

    class Meta:
        ordering = ['schedule_day__date', 'start_time', 'pk']
        constraints = [models.UniqueConstraint(fields=['schedule_day', 'source_key'], condition=models.Q(deleted__isnull=True), name='unique_live_schedule_occurrence')]


class ScheduleOccurrenceTeam(BaseModel):
    history = HistoricalRecords()
    occurrence = models.ForeignKey(ScheduleOccurrence, on_delete=models.CASCADE, related_name='team_plans')
    sub_team = models.ForeignKey('MediaSubTeam', on_delete=models.SET_NULL, null=True, related_name='+')
    team_name = models.CharField(max_length=100)
    color = models.CharField(max_length=7)
    member_count = models.PositiveSmallIntegerField(default=2)
    minimum_mentors = models.PositiveSmallIntegerField(null=True, blank=True)
    division = models.ForeignKey('ScaleDivision', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    # Activity identities and labels at planning time, not a live M2M to defaults.
    activities = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['team_name', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['occurrence', 'sub_team'], condition=models.Q(deleted__isnull=True, sub_team__isnull=False), name='unique_live_occurrence_team'),
            models.CheckConstraint(check=models.Q(member_count__gte=1), name='occurrence_team_positive_count'),
            models.CheckConstraint(check=models.Q(minimum_mentors__isnull=True) | models.Q(minimum_mentors__gte=1, minimum_mentors__lte=models.F('member_count')), name='occurrence_team_valid_mentors'),
        ]
