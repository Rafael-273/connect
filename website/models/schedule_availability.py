"""Availability declarations, independent of schedule assignments and of who enters them."""
from django.db import models
from simple_history.models import HistoricalRecords

from ._base import BaseModel


class ScheduleAvailability(BaseModel):
    history = HistoricalRecords()
    MODE_ALL = 'all'
    MODE_ONLY = 'only'
    MODE_EXCEPT = 'except'
    MODE_CHOICES = [
        (MODE_ALL, 'Disponível em todas as datas'),
        (MODE_ONLY, 'Disponível somente nestas datas'),
        (MODE_EXCEPT, 'Disponível em todas, exceto nestas datas'),
    ]
    schedule = models.ForeignKey('MonthlySchedule', on_delete=models.CASCADE, related_name='availabilities')
    sub_team = models.ForeignKey('MediaSubTeam', on_delete=models.CASCADE, related_name='schedule_availabilities')
    member = models.ForeignKey('Member', on_delete=models.CASCADE, related_name='schedule_availabilities')
    mode = models.CharField(max_length=8, choices=MODE_CHOICES)
    selected_occurrence_ids = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, related_name='+')
    recorded_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['schedule', 'sub_team', 'member'], condition=models.Q(deleted__isnull=True), name='unique_live_schedule_availability'),
            models.CheckConstraint(check=models.Q(mode__in=['all', 'only', 'except']), name='valid_schedule_availability_mode'),
        ]


class ScheduleAvailabilityOccurrence(BaseModel):
    history = HistoricalRecords()
    availability = models.ForeignKey(ScheduleAvailability, on_delete=models.CASCADE, related_name='confirmations')
    # Planning removals must not erase declarations or their original exceptions.
    occurrence = models.ForeignKey('ScheduleOccurrence', on_delete=models.DO_NOTHING, related_name='availability_confirmations')
    team_plan_id = models.PositiveBigIntegerField()
    occurrence_date = models.DateField()
    start_time = models.TimeField()
    occurrence_name = models.CharField(max_length=255)
    is_available = models.BooleanField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['availability', 'occurrence'], condition=models.Q(deleted__isnull=True), name='unique_live_availability_confirmation')]


class ScheduleAvailabilityCoverage(BaseModel):
    """Persisted availability diagnostics for future scheduling recommendations."""
    schedule = models.ForeignKey('MonthlySchedule', on_delete=models.CASCADE, related_name='availability_coverage')
    occurrence_team = models.ForeignKey('ScheduleOccurrenceTeam', on_delete=models.CASCADE, related_name='availability_coverage')
    occurrence_date = models.DateField()
    start_time = models.TimeField()
    occurrence_name = models.CharField(max_length=255)
    team_name = models.CharField(max_length=100)
    required_members = models.PositiveSmallIntegerField()
    available_members = models.PositiveSmallIntegerField(default=0)
    unavailable_members = models.PositiveSmallIntegerField(default=0)
    unknown_members = models.PositiveSmallIntegerField(default=0)
    available_mentors = models.PositiveSmallIntegerField(default=0)
    available_pending_experience = models.PositiveSmallIntegerField(default=0)
    member_shortage = models.PositiveSmallIntegerField(default=0)
    mentor_shortage = models.BooleanField(default=False)
    calculated_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['schedule', 'occurrence_team'], condition=models.Q(deleted__isnull=True),
                name='unique_live_schedule_availability_coverage',
            ),
        ]
