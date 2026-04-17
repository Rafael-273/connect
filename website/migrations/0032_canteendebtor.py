from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0031_scaledivision_schedule'),
    ]

    operations = [
        migrations.CreateModel(
            name='CanteenDebtor',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, verbose_name='Nome')),
                ('phone', models.CharField(blank=True, default='', max_length=30, verbose_name='Telefone')),
                ('purchase_date', models.DateField(default=django.utils.timezone.now, verbose_name='Data da compra')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10, verbose_name='Valor (R$)')),
                ('description', models.TextField(blank=True, default='', verbose_name='Descrição')),
                ('paid', models.BooleanField(default=False, verbose_name='Pago')),
                ('paid_date', models.DateField(blank=True, null=True, verbose_name='Data do pagamento')),
                ('notes', models.TextField(blank=True, default='', verbose_name='Observações')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Fiado',
                'verbose_name_plural': 'Fiados',
                'ordering': ['-purchase_date'],
            },
        ),
    ]
