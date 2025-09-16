#!/bin/bash

# Entrar no contêiner e executar o código Python
docker-compose exec web-project python manage.py shell << EOF
from django.utils import timezone
from django.utils.text import slugify
import datetime
from website.models.event import Event

# Definir funções
def create_recurring_event(title, description, location, event_day, event_hour, recurrence_pattern, recurrence_description):
    # Criar uma data futura que seja o dia da semana correspondente
    today = timezone.now().date()
    days_ahead = event_day - today.weekday()
    if days_ahead <= 0:  # Se é antes do dia atual da semana, vai para a próxima semana
        days_ahead += 7
    future_date = today + datetime.timedelta(days=days_ahead)
    
    # Criando o evento
    event = Event(
        title=title,
        description=description,
        event_date=future_date,
        event_time=datetime.time(event_hour, 0),  # Hora, minuto
        display_start=today,
        display_end=today + datetime.timedelta(days=365),  # Visível por 1 ano
        location=location,
        is_recurring=True,
        recurrence_pattern=recurrence_pattern,
        recurrence_description=recurrence_description
    )
    
    # Gerando um slug único
    base_slug = slugify(title)
    counter = 1
    slug = base_slug
    
    while Event.objects.filter(slug=slug).exists():
        slug = f'{base_slug}-{counter}'
        counter += 1
        
    event.slug = slug
    event.save()
    
    print(f'Evento criado: {title}')
    return event

# Criar eventos
events = [
    {
        'title': 'Culto de Domingo',
        'description': 'Nosso culto principal acontece todos os domingos pela manhã. É um momento especial para toda a família se reunir em adoração e comunhão, com músicas, pregação da Palavra e interação com a comunidade.',
        'location': 'Templo Principal - Rua das Oliveiras, 123',
        'event_day': 6,  # 0=Segunda, 6=Domingo
        'event_hour': 10,
        'recurrence_pattern': 'weekly',
        'recurrence_description': 'Todos os domingos às 10h da manhã'
    },
    {
        'title': 'Culto de Jovens',
        'description': 'Um encontro especial para os jovens da igreja com louvor, mensagem direcionada e atividades de integração. Um ambiente descontraído onde os jovens podem crescer espiritualmente e fazer amizades.',
        'location': 'Salão Multiuso - Rua das Oliveiras, 123',
        'event_day': 5,  # Sábado
        'event_hour': 19,
        'recurrence_pattern': 'weekly',
        'recurrence_description': 'Todos os sábados às 19h'
    },
    {
        'title': 'Reunião de Oração',
        'description': 'Momento dedicado à intercessão e busca espiritual, onde compartilhamos pedidos de oração e nos fortalecemos mutuamente. Uma oportunidade de aprofundar sua vida de oração.',
        'location': 'Sala de Oração - Rua das Oliveiras, 123',
        'event_day': 2,  # Quarta
        'event_hour': 19,
        'recurrence_pattern': 'weekly',
        'recurrence_description': 'Todas as quartas-feiras às 19h'
    },
    {
        'title': 'Estudo Bíblico',
        'description': 'Aprofunde seu conhecimento nas escrituras com nossos estudos temáticos e sistemáticos da Bíblia. Aberto para todos os níveis de conhecimento bíblico, com espaço para perguntas e discussões.',
        'location': 'Templo Principal - Rua das Oliveiras, 123',
        'event_day': 1,  # Terça
        'event_hour': 19,
        'recurrence_pattern': 'weekly',
        'recurrence_description': 'Todas as terças-feiras às 19h30'
    },
    {
        'title': 'Grupo de Mulheres',
        'description': 'Um espaço acolhedor para as mulheres compartilharem experiências, estudarem a Palavra e se apoiarem mutuamente. Inclui atividades especiais, palestras e momentos de confraternização.',
        'location': 'Sala Multiuso 2 - Rua das Oliveiras, 123',
        'event_day': 3,  # Quinta
        'event_hour': 14,
        'recurrence_pattern': 'biweekly',
        'recurrence_description': 'Quinzenalmente às quintas-feiras às 14h30'
    },
    {
        'title': 'Ensaio do Coral',
        'description': 'Preparação musical para os cultos e eventos especiais da igreja. Aberto para todos que desejam servir através da música, independente do nível de experiência.',
        'location': 'Salão de Música - Rua das Oliveiras, 123',
        'event_day': 4,  # Sexta
        'event_hour': 19,
        'recurrence_pattern': 'weekly',
        'recurrence_description': 'Todas as sextas-feiras às 19h'
    },
    {
        'title': 'Grupo de Intercessão Matinal',
        'description': 'Encontro matinal para oração e intercessão antes do início das atividades do dia. Um momento poderoso para começar o dia na presença de Deus.',
        'location': 'Capela - Rua das Oliveiras, 123',
        'event_day': 0,  # Segunda
        'event_hour': 6,
        'recurrence_pattern': 'weekly',
        'recurrence_description': 'Todas as segundas-feiras às 6h da manhã'
    }
]

# Limpar eventos recorrentes existentes (se necessário)
Event.objects.filter(is_recurring=True).delete()

# Criar os eventos
for event_data in events:
    create_recurring_event(**event_data)

print('Todos os 7 eventos recorrentes foram criados com sucesso!')
EOF
