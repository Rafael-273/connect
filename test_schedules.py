#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'connect.settings')
django.setup()

from website.models import User, Member, MonthlySchedule, ScheduleDay, MinistryMembership, Team
from datetime import datetime
from django.db.models import Q

# Buscar o usuário
email = 'rafaelpinheiro@gmail.com'
user = User.objects.filter(email=email).first()

if not user:
    print(f"❌ Usuário {email} não encontrado")
    exit()

print(f"✅ Usuário: {user.email}")

if not hasattr(user, 'member'):
    print("❌ Usuário não tem membro associado")
    exit()

member = user.member
print(f"✅ Membro: {member.name} (ID: {member.id})")

# Verificar ministérios (sistema antigo)
ministries_old = member.ministry.all()
print(f"\n📋 Ministérios (sistema antigo): {ministries_old.count()}")
for m in ministries_old:
    print(f"   - {m.name} (ID: {m.id})")

# Verificar ministérios (sistema novo)
ministries_new = MinistryMembership.objects.filter(member=member, is_active=True)
print(f"\n📋 Ministérios (sistema novo - MinistryMembership): {ministries_new.count()}")
for mm in ministries_new:
    print(f"   - {mm.ministry.name} (ID: {mm.ministry.id}) - Ativo: {mm.is_active}")

# IDs combinados
ministry_ids_old = list(ministries_old.values_list('id', flat=True))
ministry_ids_new = list(ministries_new.values_list('ministry_id', flat=True))
all_ministry_ids = list(set(ministry_ids_old + ministry_ids_new))

print(f"\n🔍 IDs de ministérios combinados: {all_ministry_ids}")

# Data
today = datetime.now().date()
current_month = today.month
current_year = today.year
next_month = current_month + 1 if current_month < 12 else 1
next_year = current_year if current_month < 12 else current_year + 1

print(f"\n📅 Data atual: {today}")
print(f"📅 Buscando escalas para: {current_month}/{current_year} e {next_month}/{next_year}")

# Buscar escalas
all_schedules = MonthlySchedule.objects.filter(
    Q(month=current_month, year=current_year) | Q(month=next_month, year=next_year),
    ministry_id__in=all_ministry_ids,
    is_published=True,
    deleted__isnull=True
).select_related('ministry')

print(f"\n📊 Total de escalas encontradas: {all_schedules.count()}")

for schedule in all_schedules:
    print(f"\n{'='*60}")
    print(f"📄 Escala: {schedule.ministry.name} - {schedule.title}")
    print(f"   Mês/Ano: {schedule.get_month_display()}/{schedule.year}")
    print(f"   Publicada: {schedule.is_published}")
    print(f"   ID: {schedule.id}")
    
    # Todos os dias
    all_days = ScheduleDay.objects.filter(schedule=schedule)
    print(f"   Total de dias (incluindo cancelados): {all_days.count()}")
    
    # Dias não cancelados
    active_days = all_days.filter(is_cancelled=False)
    print(f"   Dias ativos (não cancelados): {active_days.count()}")
    
    # Verificar escalação direta
    days_with_member_direct = active_days.filter(members=member)
    print(f"\n   🔹 Dias onde o membro está escalado DIRETAMENTE: {days_with_member_direct.count()}")
    for day in days_with_member_direct:
        members_list = ', '.join([m.name for m in day.members.all()])
        print(f"      - {day.date} ({day.date.strftime('%A')}): {day.description or 'Sem descrição'}")
        print(f"        Membros: {members_list}")
    
    # Verificar escalação via equipe
    days_with_member_team = active_days.filter(team__isnull=False)
    print(f"\n   🔹 Dias com equipes: {days_with_member_team.count()}")
    
    member_in_team_days = []
    for day in days_with_member_team:
        if day.team:
            team_members = day.team.members.all()
            if member in team_members:
                member_in_team_days.append(day)
                print(f"      ✅ {day.date} ({day.date.strftime('%A')}): {day.description or 'Sem descrição'}")
                print(f"         Equipe: {day.team.name}")
                members_names = ', '.join([m.name for m in team_members])
                print(f"         Membros da equipe: {members_names}")
    
    print(f"\n   🔹 Dias onde o membro está na EQUIPE: {len(member_in_team_days)}")
    
    # Total
    has_schedule = active_days.filter(
        Q(members=member) | Q(team__members=member)
    ).exists()
    
    total_days_with_member = days_with_member_direct.count() + len(member_in_team_days)
    
    print(f"\n   {'✅' if has_schedule else '❌'} MEMBRO ESTÁ ESCALADO: {has_schedule}")
    print(f"   Total de dias com o membro: {total_days_with_member}")

print(f"\n{'='*60}")
print("✅ Análise completa!")
