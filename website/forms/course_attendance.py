from django import forms

from ..models.course_attendance import AttendanceCourse, CourseLesson, CourseParticipant
from ..models.member import Member


class AttendanceCourseForm(forms.ModelForm):
    class Meta:
        model = AttendanceCourse
        fields = [
            'name',
            'description',
            'start_date',
            'end_date',
            'recurrence_type',
            'recurrence_weekday',
            'recurrence_interval_days',
            'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Nome do curso'}),
            'description': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 4, 'placeholder': 'Descricao do curso'}),
            'start_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'end_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'recurrence_type': forms.Select(attrs={'class': 'form-select'}),
            'recurrence_weekday': forms.Select(attrs={'class': 'form-select'}),
            'recurrence_interval_days': forms.NumberInput(attrs={'class': 'form-input', 'min': 2, 'placeholder': 'Ex: 15'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        recurrence_type = cleaned_data.get('recurrence_type')
        recurrence_weekday = cleaned_data.get('recurrence_weekday')
        recurrence_interval_days = cleaned_data.get('recurrence_interval_days')

        requires_weekday = {
            AttendanceCourse.RECURRENCE_WEEKLY,
            AttendanceCourse.RECURRENCE_BIWEEKLY,
        }

        if recurrence_type in requires_weekday and not recurrence_weekday:
            self.add_error('recurrence_weekday', 'Selecione o dia da semana para essa recorrencia.')

        if recurrence_type == AttendanceCourse.RECURRENCE_CUSTOM_DAYS:
            if not recurrence_interval_days:
                self.add_error('recurrence_interval_days', 'Informe o intervalo em dias.')
            elif recurrence_interval_days < 2:
                self.add_error('recurrence_interval_days', 'O intervalo deve ser maior ou igual a 2 dias.')

        if recurrence_type not in requires_weekday:
            cleaned_data['recurrence_weekday'] = None

        if recurrence_type != AttendanceCourse.RECURRENCE_CUSTOM_DAYS:
            cleaned_data['recurrence_interval_days'] = None

        return cleaned_data


class CourseParticipantForm(forms.ModelForm):
    member = forms.ModelChoiceField(
        queryset=Member.objects.filter(is_active=True).order_by('name'),
        required=False,
        empty_label='Selecione um membro (opcional)',
        widget=forms.Select(attrs={'class': 'form-input'}),
    )

    class Meta:
        model = CourseParticipant
        fields = ['member', 'full_name', 'phone', 'email']
        widgets = {
            'full_name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Nome completo'}),
            'phone': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Telefone'}),
            'email': forms.EmailInput(attrs={'class': 'form-input', 'placeholder': 'Email'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['full_name'].required = False

    def clean(self):
        cleaned_data = super().clean()
        member = cleaned_data.get('member')
        full_name = (cleaned_data.get('full_name') or '').strip()

        if not member and not full_name:
            raise forms.ValidationError('Informe um membro ou o nome do participante.')

        if member and not full_name:
            cleaned_data['full_name'] = member.name

        return cleaned_data


class CourseLessonForm(forms.ModelForm):
    class Meta:
        model = CourseLesson
        fields = ['title', 'lesson_date', 'notes', 'was_held']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Titulo da aula'}),
            'lesson_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3, 'placeholder': 'Observacoes da aula'}),
            'was_held': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }
