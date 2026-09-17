from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('website', '0077_speech_filler_terms')]

    operations = [
        migrations.AddField(
            model_name='mediatemplateblock',
            name='remove_background_voice',
            field=models.BooleanField(
                default=False,
                help_text='Remove falas isoladas de um entrevistador/voz sem microfone. Sobreposições são preservadas.',
                verbose_name='Remover voz de fundo',
            ),
        ),
    ]
