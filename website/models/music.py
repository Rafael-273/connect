from django.db import models
from ._base import BaseModel


class Music(BaseModel):
    TEMPO_CHOICES = [
        ('rapida', 'Rápida'),
        ('media', 'Média'),
        ('lenta', 'Lenta'),
    ]

    name = models.CharField(max_length=200)
    singer = models.CharField(max_length=200)
    chord_sheet = models.FileField(
        upload_to='chord_sheet/',
        null=True,
        blank=True,
        help_text='(Será descontinuado - use a seção de Cifras abaixo)'
    )
    tempo = models.CharField(
        max_length=10,
        choices=TEMPO_CHOICES,
        blank=True,
        default='',
        verbose_name='Andamento',
    )
    audio_file = models.FileField(
        upload_to='music/audio/',
        max_length=255,
        blank=True,
        help_text='Arquivo de áudio usado como trilha ou referência sonora.',
    )

    def __str__(self):
        return f"{self.name} - {self.singer}"
    
    def get_first_chord_sheet(self):
        """Retorna a primeira cifra ou o arquivo antigo"""
        chord = self.chordsheets.first()
        if chord:
            return chord.file
        return self.chord_sheet


class ChordSheet(BaseModel):
    music = models.ForeignKey(
        Music,
        on_delete=models.CASCADE,
        related_name='chordsheets'
    )
    file = models.FileField(
        upload_to='chord_sheet/',
        max_length=255,
        help_text='Arquivo PDF da cifra'
    )
    tone = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name='Tom',
        help_text='Ex: Dó, Mi, Sol (opcional)'
    )
    order = models.PositiveIntegerField(
        default=0,
        help_text='Ordem de exibição'
    )

    class Meta:
        ordering = ['order']

    def save(self, *args, **kwargs):
        # Se é um novo registro e não tem ordem definida, define automaticamente
        if not self.pk and self.order == 0:
            last_order = ChordSheet.objects.filter(music=self.music).aggregate(
                max_order=models.Max('order')
            )['max_order']
            self.order = (last_order or 0) + 1
        super().save(*args, **kwargs)

    def __str__(self):
        tone_display = f" - Tom: {self.tone}" if self.tone else ""
        return f"{self.music.name}{tone_display}"
