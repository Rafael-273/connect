from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0058_music_audio_and_template_background_music'),
    ]

    operations = [
        migrations.AlterField(
            model_name='mediatemplateplugin',
            name='code',
            field=models.CharField(
                choices=[
                    ('silence_removal', 'Corte de silêncio'),
                    ('filler_removal', 'Remover vícios de fala'),
                    ('auto_tracking', 'Auto Reframe inteligente'),
                    ('subtitle_pt', 'Legenda PT'),
                    ('translation_en', 'Tradução EN'),
                    ('lut', 'Aplicar LUT'),
                    ('intro', 'Intro'),
                    ('outro', 'Tela final'),
                    ('music', 'Música'),
                ],
                max_length=32,
            ),
        ),
    ]
