from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0097_merge_0052_visitor_wants_house_of_peace_0096_project_configuration_decimal_encoder'),
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
                    ('off_context_detection', 'Detectar trechos fora de contexto'),
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
