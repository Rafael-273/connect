from django.db import migrations, models
import django.db.models.deletion
import datetime


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0033_visitor_add_birth_date'),
    ]

    operations = [
        migrations.CreateModel(
            name='AttendanceCourse',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('update_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=160)),
                ('description', models.TextField(blank=True, null=True)),
                ('start_date', models.DateField(default=datetime.date.today)),
                ('end_date', models.DateField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Curso de Presenca',
                'verbose_name_plural': 'Cursos de Presenca',
                'ordering': ['-start_date', 'name'],
            },
        ),
        migrations.CreateModel(
            name='CourseParticipant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('update_at', models.DateTimeField(auto_now=True)),
                ('full_name', models.CharField(max_length=180)),
                ('phone', models.CharField(blank=True, max_length=32, null=True)),
                ('email', models.EmailField(blank=True, max_length=254, null=True)),
                ('course', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='participants', to='website.attendancecourse')),
                ('member', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='course_participations', to='website.member')),
            ],
            options={
                'verbose_name': 'Participante de Curso',
                'verbose_name_plural': 'Participantes de Curso',
                'ordering': ['full_name'],
            },
        ),
        migrations.CreateModel(
            name='CourseLesson',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('update_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=180)),
                ('lesson_date', models.DateField()),
                ('notes', models.TextField(blank=True, null=True)),
                ('course', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lessons', to='website.attendancecourse')),
            ],
            options={
                'verbose_name': 'Aula de Curso',
                'verbose_name_plural': 'Aulas de Curso',
                'ordering': ['lesson_date', 'title'],
            },
        ),
        migrations.CreateModel(
            name='CourseAttendance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('update_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('present', 'Presente'), ('absent', 'Faltou'), ('excused', 'Justificada')], default='present', max_length=16)),
                ('notes', models.CharField(blank=True, max_length=220, null=True)),
                ('lesson', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attendance_records', to='website.courselesson')),
                ('participant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attendance_records', to='website.courseparticipant')),
            ],
            options={
                'verbose_name': 'Presenca em Aula',
                'verbose_name_plural': 'Presencas em Aulas',
                'ordering': ['lesson__lesson_date', 'participant__full_name'],
                'unique_together': {('lesson', 'participant')},
            },
        ),
    ]
