from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify
from django.core.files import File
from website.models.event import Event
import datetime
import os

class Command(BaseCommand):
    help = 'Cria 7 eventos recorrentes para testes'

    def handle(self, *args, **options):
        # Função para criar um evento recorrente
        def create_recurring_event(title, description, location, event_date, event_time, recurrence_pattern, recurrence_description=None):
            # Criando a data atual para o período de exibição (1 ano)
            today = timezone.now().date()
            one_year_later = today + datetime.timedelta(days=365)
            
            # Criando o slug único
            base_slug = slugify(title)
            slug = base_slug
            counter = 1
            
            # Garantir que o slug seja único
            while Event.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            
            # Criando o evento
            event = Event(
                title=title,
                description=description,
                event_date=event_date,
                event_time=event_time,
                display_start=today,
                display_end=one_year_later,
                location=location,
                is_recurring=True,
                recurrence_pattern=recurrence_pattern,
                recurrence_description=recurrence_description,
                slug=slug
            )
            
            # Salvando o evento para poder adicionar o banner
            event.save()
            
            # Adicionando o banner default
            self.stdout.write(self.style.SUCCESS(f"Evento criado: {title}"))
                
            return event

        # Definindo os dias da semana para os eventos
        segunda = datetime.date(2025, 9, 15)  # Uma segunda-feira de exemplo
        terca = datetime.date(2025, 9, 16)
        quarta = datetime.date(2025, 9, 17)
        quinta = datetime.date(2025, 9, 18)
        sexta = datetime.date(2025, 9, 19)
        sabado = datetime.date(2025, 9, 20)
        domingo = datetime.date(2025, 9, 21)

        # Criando os 7 eventos recorrentes
        events = [
            {
                "title": "Culto de Domingo",
                "description": "Nosso culto principal acontece todos os domingos pela manhã. É um momento especial para toda a família se reunir em adoração e comunhão, com músicas, pregação da Palavra e interação com a comunidade.",
                "location": "Templo Principal - Rua das Oliveiras, 123",
                "event_date": domingo,
                "event_time": datetime.time(10, 0),  # 10:00
                "recurrence_pattern": "weekly",
                "recurrence_description": "Todos os domingos às 10h da manhã"
            },
            {
                "title": "Culto de Jovens",
                "description": "Um encontro especial para os jovens da igreja com louvor, mensagem direcionada e atividades de integração. Um ambiente descontraído onde os jovens podem crescer espiritualmente e fazer amizades.",
                "location": "Salão Multiuso - Rua das Oliveiras, 123",
                "event_date": sabado,
                "event_time": datetime.time(19, 30),  # 19:30
                "recurrence_pattern": "weekly",
                "recurrence_description": "Todos os sábados às 19h30"
            },
            {
                "title": "Reunião de Oração",
                "description": "Momento dedicado à intercessão e busca espiritual, onde compartilhamos pedidos de oração e nos fortalecemos mutuamente. Uma oportunidade de aprofundar sua vida de oração.",
                "location": "Sala de Oração - Rua das Oliveiras, 123",
                "event_date": quarta,
                "event_time": datetime.time(19, 0),  # 19:00
                "recurrence_pattern": "weekly",
                "recurrence_description": "Todas as quartas-feiras às 19h"
            },
            {
                "title": "Estudo Bíblico",
                "description": "Aprofunde seu conhecimento nas escrituras com nossos estudos temáticos e sistemáticos da Bíblia. Aberto para todos os níveis de conhecimento bíblico, com espaço para perguntas e discussões.",
                "location": "Templo Principal - Rua das Oliveiras, 123",
                "event_date": terca,
                "event_time": datetime.time(19, 30),  # 19:30
                "recurrence_pattern": "weekly",
                "recurrence_description": "Todas as terças-feiras às 19h30"
            },
            {
                "title": "Grupo de Mulheres",
                "description": "Um espaço acolhedor para as mulheres compartilharem experiências, estudarem a Palavra e se apoiarem mutuamente. Inclui atividades especiais, palestras e momentos de confraternização.",
                "location": "Sala Multiuso 2 - Rua das Oliveiras, 123",
                "event_date": quinta,
                "event_time": datetime.time(14, 30),  # 14:30
                "recurrence_pattern": "biweekly",
                "recurrence_description": "Quinzenalmente às quintas-feiras às 14h30"
            },
            {
                "title": "Ensaio do Coral",
                "description": "Preparação musical para os cultos e eventos especiais da igreja. Aberto para todos que desejam servir através da música, independente do nível de experiência.",
                "location": "Salão de Música - Rua das Oliveiras, 123",
                "event_date": sexta,
                "event_time": datetime.time(19, 0),  # 19:00
                "recurrence_pattern": "weekly",
                "recurrence_description": "Todas as sextas-feiras às 19h"
            },
            {
                "title": "Grupo de Intercessão Matinal",
                "description": "Encontro matinal para oração e intercessão antes do início das atividades do dia. Um momento poderoso para começar o dia na presença de Deus.",
                "location": "Capela - Rua das Oliveiras, 123",
                "event_date": segunda,
                "event_time": datetime.time(6, 0),  # 06:00
                "recurrence_pattern": "weekly",
                "recurrence_description": "Todas as segundas-feiras às 6h da manhã"
            }
        ]

        # Criar os eventos
        for event_data in events:
            create_recurring_event(**event_data)

        self.stdout.write(self.style.SUCCESS('Todos os 7 eventos recorrentes foram criados com sucesso!'))
