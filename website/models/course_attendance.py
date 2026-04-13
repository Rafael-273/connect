from datetime import date

from django.db import models

from .member import Member


class AttendanceCourse(models.Model):
    RECURRENCE_NONE = 'none'
    RECURRENCE_WEEKLY = 'weekly'
    RECURRENCE_BIWEEKLY = 'biweekly'
    RECURRENCE_CUSTOM_DAYS = 'custom_days'

    RECURRENCE_CHOICES = [
        (RECURRENCE_NONE, 'Sem recorrencia fixa'),
        (RECURRENCE_WEEKLY, 'Semanal'),
        (RECURRENCE_BIWEEKLY, 'Quinzenal'),
        (RECURRENCE_CUSTOM_DAYS, 'A cada X dias'),
    ]

    WEEKDAY_MONDAY = 'monday'
    WEEKDAY_TUESDAY = 'tuesday'
    WEEKDAY_WEDNESDAY = 'wednesday'
    WEEKDAY_THURSDAY = 'thursday'
    WEEKDAY_FRIDAY = 'friday'
    WEEKDAY_SATURDAY = 'saturday'
    WEEKDAY_SUNDAY = 'sunday'

    WEEKDAY_CHOICES = [
        (WEEKDAY_MONDAY, 'Segunda-feira'),
        (WEEKDAY_TUESDAY, 'Terca-feira'),
        (WEEKDAY_WEDNESDAY, 'Quarta-feira'),
        (WEEKDAY_THURSDAY, 'Quinta-feira'),
        (WEEKDAY_FRIDAY, 'Sexta-feira'),
        (WEEKDAY_SATURDAY, 'Sabado'),
        (WEEKDAY_SUNDAY, 'Domingo'),
    ]

    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, null=True)
    start_date = models.DateField(default=date.today)
    end_date = models.DateField(blank=True, null=True)
    recurrence_type = models.CharField(
        max_length=20,
        choices=RECURRENCE_CHOICES,
        default=RECURRENCE_NONE,
    )
    recurrence_weekday = models.CharField(
        max_length=12,
        choices=WEEKDAY_CHOICES,
        blank=True,
        null=True,
    )
    recurrence_interval_days = models.PositiveSmallIntegerField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    update_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Curso de Presenca'
        verbose_name_plural = 'Cursos de Presenca'
        ordering = ['-start_date', 'name']

    def __str__(self):
        return self.name

    def recurrence_display(self):
        if self.recurrence_type == self.RECURRENCE_WEEKLY and self.recurrence_weekday:
            return f'Semanal - {self.get_recurrence_weekday_display()}'
        if self.recurrence_type == self.RECURRENCE_BIWEEKLY and self.recurrence_weekday:
            return f'Quinzenal - {self.get_recurrence_weekday_display()}'
        if self.recurrence_type == self.RECURRENCE_CUSTOM_DAYS and self.recurrence_interval_days:
            return f'A cada {self.recurrence_interval_days} dias'
        return 'Sem recorrencia fixa'


class CourseParticipant(models.Model):
    course = models.ForeignKey(
        AttendanceCourse,
        on_delete=models.CASCADE,
        related_name='participants',
    )
    member = models.ForeignKey(
        Member,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='course_participations',
    )
    full_name = models.CharField(max_length=180)
    phone = models.CharField(max_length=32, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    update_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Participante de Curso'
        verbose_name_plural = 'Participantes de Curso'
        ordering = ['full_name']

    def __str__(self):
        return f'{self.full_name} ({self.course.name})'


class CourseLesson(models.Model):
    course = models.ForeignKey(
        AttendanceCourse,
        on_delete=models.CASCADE,
        related_name='lessons',
    )
    title = models.CharField(max_length=180)
    lesson_date = models.DateField()
    notes = models.TextField(blank=True, null=True)
    was_held = models.BooleanField(default=True)
    based_on_recurrence = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    update_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Aula de Curso'
        verbose_name_plural = 'Aulas de Curso'
        ordering = ['lesson_date', 'title']

    def __str__(self):
        suffix = '' if self.was_held else ' (Sem aula)'
        return f'{self.title} - {self.lesson_date:%d/%m/%Y}{suffix}'


class CourseAttendance(models.Model):
    STATUS_PRESENT = 'present'
    STATUS_ABSENT = 'absent'
    STATUS_EXCUSED = 'excused'

    STATUS_CHOICES = [
        (STATUS_PRESENT, 'Presente'),
        (STATUS_ABSENT, 'Faltou'),
        (STATUS_EXCUSED, 'Justificada'),
    ]

    lesson = models.ForeignKey(
        CourseLesson,
        on_delete=models.CASCADE,
        related_name='attendance_records',
    )
    participant = models.ForeignKey(
        CourseParticipant,
        on_delete=models.CASCADE,
        related_name='attendance_records',
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PRESENT)
    notes = models.CharField(max_length=220, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    update_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Presenca em Aula'
        verbose_name_plural = 'Presencas em Aulas'
        unique_together = ('lesson', 'participant')
        ordering = ['lesson__lesson_date', 'participant__full_name']

    def __str__(self):
        return f'{self.participant.full_name} - {self.lesson.title} ({self.get_status_display()})'

    @classmethod
    def export_header(cls):
        return [
            'Curso',
            'Aula',
            'Data da Aula',
            'Participante',
            'Telefone',
            'Email',
            'Status',
            'Observacoes',
        ]

    @classmethod
    def export_row(cls, record):
        participant = record.participant
        return [
            record.lesson.course.name,
            record.lesson.title,
            record.lesson.lesson_date.strftime('%d/%m/%Y'),
            participant.full_name,
            participant.phone or '',
            participant.email or '',
            record.get_status_display(),
            record.notes or '',
        ]
