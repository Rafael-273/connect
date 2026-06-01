from django import forms

from website.models.event import Event
from website.models.media_attachment import MediaAttachment
from website.models.media_comment import MediaComment
from website.models.media_content import MediaContent
from website.models.media_task import MediaTask
from website.models.user import User

_INPUT = 'form-input'
_SELECT = 'form-input'
_TEXTAREA = (
    'w-full rounded-xl border border-gray-300 px-4 py-3 text-sm '
    'focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] '
    'focus:border-transparent resize-none'
)


class MediaContentForm(forms.ModelForm):
    class Meta:
        model = MediaContent
        fields = [
            'title', 'description', 'content_type', 'event',
            'publication_date', 'due_date', 'responsible', 'status', 'priority',
        ]
        widgets = {
            'title': forms.TextInput(attrs={
                'class': _INPUT,
                'placeholder': 'Ex: Arte do culto de domingo',
            }),
            'description': forms.Textarea(attrs={
                'class': _TEXTAREA,
                'placeholder': 'Descreva o conteúdo, referências ou observações...',
                'rows': 3,
            }),
            'content_type': forms.Select(attrs={'class': _SELECT}),
            'event': forms.Select(attrs={'class': _SELECT}),
            'publication_date': forms.DateTimeInput(
                attrs={'class': _INPUT, 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'due_date': forms.DateTimeInput(
                attrs={'class': _INPUT, 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'responsible': forms.Select(attrs={'class': _SELECT}),
            'status': forms.Select(attrs={'class': _SELECT}),
            'priority': forms.Select(attrs={'class': _SELECT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['event'].queryset = Event.objects.order_by('-event_date')
        self.fields['event'].empty_label = '— Sem evento vinculado —'
        self.fields['responsible'].queryset = (
            User.objects.filter(member__isnull=False).order_by('member__name')
        )
        self.fields['responsible'].empty_label = '— Selecione o responsável —'
        self.fields['publication_date'].input_formats = ['%Y-%m-%dT%H:%M']
        self.fields['due_date'].input_formats = ['%Y-%m-%dT%H:%M']


class MediaTaskForm(forms.ModelForm):
    class Meta:
        model = MediaTask
        fields = ['title', 'description', 'assigned_to', 'due_date', 'status']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': _INPUT,
                'placeholder': 'Ex: Criar arte base',
            }),
            'description': forms.Textarea(attrs={
                'class': _TEXTAREA,
                'placeholder': 'Detalhes da tarefa...',
                'rows': 2,
            }),
            'assigned_to': forms.Select(attrs={'class': _SELECT}),
            'due_date': forms.DateTimeInput(
                attrs={'class': _INPUT, 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'status': forms.Select(attrs={'class': _SELECT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assigned_to'].queryset = (
            User.objects.filter(member__isnull=False).order_by('member__name')
        )
        self.fields['assigned_to'].empty_label = '— Selecionar membro —'
        self.fields['due_date'].input_formats = ['%Y-%m-%dT%H:%M']


class MediaCommentForm(forms.ModelForm):
    class Meta:
        model = MediaComment
        fields = ['text']
        widgets = {
            'text': forms.Textarea(attrs={
                'class': _TEXTAREA,
                'placeholder': 'Escreva um comentário...',
                'rows': 2,
            }),
        }


class MediaAttachmentForm(forms.ModelForm):
    class Meta:
        model = MediaAttachment
        fields = ['name', 'file']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': _INPUT,
                'placeholder': 'Nome do arquivo',
            }),
            'file': forms.ClearableFileInput(attrs={'class': _INPUT}),
        }
