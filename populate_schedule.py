#!/usr/bin/env python
"""
Script para popular uma escala de teste com vários dias e membros
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'connect.settings')
django.setup()

from website.models import MonthlySchedule, ScheduleDay, Member, Team, Ministry, User
from datetime import date

def populate_schedule():
    # Buscar ou criar um ministério
    ministry, _ = Ministry.objects.get_or_create(
        name="Música",
        defaults={"description": "Ministério de música e louvor"}
    )

    # Buscar ou criar uma escala de fevereiro 2026
    schedule, created = MonthlySchedule.objects.get_or_create(
        month=2,
        year=2026,
        ministry=ministry,
        defaults={
            "title": "Escala de Louvor - Fevereiro",
            "guidelines": "• Chegar 30min antes do culto\n• Ensaio toda quinta-feira às 19h\n• Trazer instrumento próprio\n• Confirmar presença no grupo"
        }
    )

    print(f"📅 Escala: {schedule.title} ({'criada' if created else 'já existe'})")

    # Criar equipes se não existirem
    team1, _ = Team.objects.get_or_create(
        name="Equipe Alpha",
        ministry=ministry
    )

    team2, _ = Team.objects.get_or_create(
        name="Equipe Beta",
        ministry=ministry
    )

    print(f"👥 Equipes: {team1.name}, {team2.name}")

    # Criar alguns membros de teste se não existirem
    members_data = [
        ("João Silva", "joao.silva@filadelfia.com"),
        ("Maria Santos", "maria.santos@filadelfia.com"),
        ("Pedro Oliveira", "pedro.oliveira@filadelfia.com"),
        ("Ana Costa", "ana.costa@filadelfia.com"),
        ("Carlos Souza", "carlos.souza@filadelfia.com"),
        ("Juliana Lima", "juliana.lima@filadelfia.com"),
    ]

    members = []
    for name, email in members_data:
        user, _ = User.objects.get_or_create(
            email=email,
            defaults={
                "first_name": name.split()[0],
                "last_name": " ".join(name.split()[1:]),
            }
        )
        
        member, _ = Member.objects.get_or_create(
            user=user,
            defaults={
                "name": name,
                "phone": f"(11) 9{9000 + len(members):04d}-0000",
                "birth_date": date(1990, 1, 1)
            }
        )
        members.append(member)

    print(f"✅ {len(members)} membros verificados/criados")

    # Limpar dias existentes desta escala
    deleted_count = ScheduleDay.objects.filter(schedule=schedule).delete()[0]
    if deleted_count > 0:
        print(f"🗑️  {deleted_count} dias anteriores removidos")

    # Criar dias de fevereiro 2026 (domingos e quartas)
    days_to_create = [
        (date(2026, 2, 1), team1, [members[0], members[1]], "Culto de Domingo - Manhã 9h"),
        (date(2026, 2, 4), team2, [members[2], members[3]], "Culto de Quarta - Noite 19h30"),
        (date(2026, 2, 8), team1, [members[0], members[4]], "Culto de Domingo - Manhã 9h"),
        (date(2026, 2, 11), team2, [members[1], members[5]], "Culto de Quarta - Noite 19h30"),
        (date(2026, 2, 15), team1, [members[2], members[3], members[4]], "Culto de Domingo - Manhã 9h"),
        (date(2026, 2, 18), team2, [members[0], members[5]], "Culto de Quarta - Noite 19h30"),
        (date(2026, 2, 22), team1, [members[1], members[2]], "Culto de Domingo - Manhã 9h"),
        (date(2026, 2, 25), team2, [members[3], members[4], members[5]], "Culto de Quarta - Noite 19h30"),
    ]

    print("\n📝 Criando dias da escala:")
    print("─" * 60)
    
    created_days = []
    for day_date, team, day_members, description in days_to_create:
        day = ScheduleDay.objects.create(
            schedule=schedule,
            date=day_date,
            team=team,
            description=description
        )
        day.members.set(day_members)
        created_days.append(day)
        
        member_names = ", ".join([m.name for m in day_members])
        print(f"✅ {day_date.strftime('%d/%m/%Y')} ({day_date.strftime('%A')})")
        print(f"   Equipe: {team.name}")
        print(f"   Membros: {member_names}")
        print(f"   Descrição: {description}")
        print()

    print("─" * 60)
    print(f"🎉 Total: {len(created_days)} dias criados!")
    print(f"\n📊 Resumo:")
    print(f"   • Escala ID: {schedule.id}")
    print(f"   • Mês/Ano: {schedule.get_month_display()}/{schedule.year}")
    print(f"   • Ministério: {ministry.name}")
    print(f"   • Total de membros: {len(members)}")
    print(f"\n🔗 Acesse: /member/schedule/{schedule.id}/")

if __name__ == '__main__':
    populate_schedule()
