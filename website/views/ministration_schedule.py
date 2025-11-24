from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.conf import settings
from datetime import datetime, timedelta
from website.models import MinistrationSchedule, Member, Ministry
from website.forms import MinistrationScheduleForm
from weasyprint import HTML
import calendar
import os


@login_required
def schedule_list(request):
    """Lista todas as escalas de ministração"""
    if not request.user.is_staff:
        messages.error(request, 'Acesso negado. Apenas administradores podem acessar esta página.')
        return redirect('admin_dashboard')
    
    schedules = MinistrationSchedule.objects.all().prefetch_related('members', 'leaders')
    
    context = {
        'schedules': schedules,
        'title': 'Escalas de Ministração'
    }
    return render(request, 'admin_panel/ministration/schedule_list.html', context)


@login_required
def schedule_create(request):
    """Cria uma nova escala de ministração"""
    if not request.user.is_staff:
        messages.error(request, 'Acesso negado.')
        return redirect('admin_dashboard')
    
    if request.method == 'POST':
        form = MinistrationScheduleForm(request.POST)
        if form.is_valid():
            schedule = form.save()
            messages.success(request, f'Escala criada com sucesso para a semana de {schedule.week_start_date.strftime("%d/%m/%Y")}!')
            return redirect('ministration_schedule_list')
    else:
        # Sugerir a próxima segunda-feira como data inicial
        today = datetime.now().date()
        days_until_monday = (7 - today.weekday()) % 7
        next_monday = today + timedelta(days=days_until_monday if days_until_monday > 0 else 7)
        
        form = MinistrationScheduleForm(initial={'week_start_date': next_monday})
    
    context = {
        'form': form,
        'title': 'Nova Escala de Ministração'
    }
    return render(request, 'admin_panel/ministration/schedule_form.html', context)


@login_required
def schedule_edit(request, schedule_id):
    """Edita uma escala de ministração existente"""
    if not request.user.is_staff:
        messages.error(request, 'Acesso negado.')
        return redirect('admin_dashboard')
    
    schedule = get_object_or_404(MinistrationSchedule, id=schedule_id)
    
    if request.method == 'POST':
        form = MinistrationScheduleForm(request.POST, instance=schedule)
        if form.is_valid():
            schedule = form.save()
            messages.success(request, 'Escala atualizada com sucesso!')
            return redirect('ministration_schedule_list')
    else:
        form = MinistrationScheduleForm(instance=schedule)
    
    context = {
        'form': form,
        'schedule': schedule,
        'title': 'Editar Escala de Ministração'
    }
    return render(request, 'admin_panel/ministration/schedule_form.html', context)


@login_required
def schedule_delete(request, schedule_id):
    """Deleta uma escala de ministração"""
    if not request.user.is_staff:
        messages.error(request, 'Acesso negado.')
        return redirect('admin_dashboard')
    
    schedule = get_object_or_404(MinistrationSchedule, id=schedule_id)
    
    if request.method == 'POST':
        week_date = schedule.week_start_date.strftime('%d/%m/%Y')
        schedule.delete()
        messages.success(request, f'Escala da semana de {week_date} deletada com sucesso!')
        return redirect('ministration_schedule_list')
    
    context = {
        'schedule': schedule,
        'title': 'Confirmar Exclusão'
    }
    return render(request, 'admin_panel/ministration/schedule_delete.html', context)


@login_required
def schedule_detail(request, schedule_id):
    """Visualiza detalhes de uma escala"""
    if not request.user.is_staff:
        messages.error(request, 'Acesso negado.')
        return redirect('admin_dashboard')
    
    schedule = get_object_or_404(MinistrationSchedule, id=schedule_id)
    
    context = {
        'schedule': schedule,
        'wednesday_date': schedule.get_wednesday_date(),
        'sunday_date': schedule.get_sunday_date(),
        'title': f'Escala {schedule.week_start_date.strftime("%d/%m/%Y")}'
    }
    return render(request, 'admin_panel/ministration/schedule_detail.html', context)


@login_required
def export_monthly_schedule_pdf(request):
    """Exporta as escalas do mês em PDF"""
    if not request.user.is_staff:
        messages.error(request, 'Acesso negado.')
        return redirect('admin_dashboard')
    
    # Obter mês e ano (padrão: mês atual)
    today = datetime.now()
    month = int(request.GET.get('month', today.month))
    year = int(request.GET.get('year', today.year))
    
    # Obter primeiro e último dia do mês
    first_day = datetime(year, month, 1).date()
    last_day = datetime(year, month, calendar.monthrange(year, month)[1]).date()
    
    # Buscar todas as escalas do mês
    schedules = MinistrationSchedule.objects.filter(
        week_start_date__gte=first_day,
        week_start_date__lte=last_day
    ).prefetch_related('members', 'leaders').order_by('week_start_date')
    
    # Nome do mês em português
    month_names = {
        1: 'Janeiro', 2: 'Fevereiro', 3: 'Março', 4: 'Abril',
        5: 'Maio', 6: 'Junho', 7: 'Julho', 8: 'Agosto',
        9: 'Setembro', 10: 'Outubro', 11: 'Novembro', 12: 'Dezembro'
    }
    month_name = month_names[month]
    
    # Caminho da fonte
    font_path = os.path.join(settings.BASE_DIR, 'static', 'fonts', 'Integral.otf')
    
    context = {
        'schedules': schedules,
        'month_name': month_name,
        'year': year,
        'generation_date': today.strftime('%d/%m/%Y às %H:%M'),
        'font_path': f'file://{font_path}',
    }
    
    # Renderizar template HTML
    html_string = render_to_string('admin_panel/ministration/schedule_pdf.html', context)
    
    # Gerar PDF com base_url para resolver paths relativos
    html = HTML(string=html_string, base_url=request.build_absolute_uri('/'))
    pdf = html.write_pdf()
    
    # Retornar resposta com PDF
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Escala_Ministracao_{month_name}_{year}.pdf"'
    
    return response
