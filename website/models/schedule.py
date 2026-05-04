from django.db import models
from django.core.exceptions import ValidationError
from datetime import datetime
from calendar import monthrange
from ._base import BaseModel


class Team(BaseModel):
    """Equipe de membros para rotação em escalas"""
    
    name = models.CharField(
        max_length=100,
        verbose_name='Nome da Equipe'
    )
    ministry = models.ForeignKey(
        'Ministry',
        on_delete=models.CASCADE,
        related_name='teams',
        verbose_name='Ministério'
    )
    members = models.ManyToManyField(
        'Member',
        related_name='teams',
        verbose_name='Membros'
    )
    leader = models.ForeignKey(
        'Member',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='led_teams',
        verbose_name='Líder da Equipe'
    )
    color = models.CharField(
        max_length=7,
        default='#3B82F6',
        verbose_name='Cor',
        help_text='Cor para identificação visual'
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name='Ativa'
    )
    
    class Meta:
        verbose_name = 'Equipe'
        verbose_name_plural = 'Equipes'
        ordering = ['ministry__name', 'name']
        unique_together = [['ministry', 'name']]
    
    def __str__(self):
        return f"{self.ministry.name} - {self.name}"
    
    def get_member_count(self):
        return self.members.count()


class MonthlySchedule(BaseModel):
    """Escala de um ministério — mensal ou semanal recorrente."""

    TYPE_MONTHLY = 'monthly'
    TYPE_WEEKLY = 'weekly'
    TYPE_CHOICES = [
        (TYPE_MONTHLY, 'Mensal'),
        (TYPE_WEEKLY, 'Semanal'),
    ]

    MONTH_CHOICES = [
        (1, 'Janeiro'), (2, 'Fevereiro'), (3, 'Março'), (4, 'Abril'),
        (5, 'Maio'), (6, 'Junho'), (7, 'Julho'), (8, 'Agosto'),
        (9, 'Setembro'), (10, 'Outubro'), (11, 'Novembro'), (12, 'Dezembro')
    ]

    WEEK_DAYS_CHOICES = [
        (0, 'Segunda-feira'),
        (1, 'Terça-feira'),
        (2, 'Quarta-feira'),
        (3, 'Quinta-feira'),
        (4, 'Sexta-feira'),
        (5, 'Sábado'),
        (6, 'Domingo'),
    ]

    # Tipo da escala
    schedule_type = models.CharField(
        max_length=10,
        choices=TYPE_CHOICES,
        default=TYPE_MONTHLY,
        verbose_name='Tipo',
    )

    ministry = models.ForeignKey(
        'Ministry',
        on_delete=models.CASCADE,
        related_name='schedules',
        verbose_name='Ministério'
    )
    title = models.CharField(
        max_length=200,
        verbose_name='Título',
        help_text='Ex: Fotografia, Gravação, Cultos, Ensaios'
    )

    # Campos exclusivos da escala MENSAL
    month = models.IntegerField(
        choices=MONTH_CHOICES,
        verbose_name='Mês',
        blank=True,
        null=True,
    )
    year = models.IntegerField(
        verbose_name='Ano',
        blank=True,
        null=True,
    )

    # Campos exclusivos da escala SEMANAL
    start_date = models.DateField(
        blank=True,
        null=True,
        verbose_name='Válida a partir de',
        help_text='Primeira semana desta escala semanal',
    )
    end_date = models.DateField(
        blank=True,
        null=True,
        verbose_name='Válida até',
        help_text='Deixe vazio para vigência indefinida',
    )
    # Dias da semana que terão escala, ex: "0,2,6" = seg, qua, dom
    days_of_week = models.CharField(
        max_length=13,
        blank=True,
        null=True,
        verbose_name='Dias da Semana',
        help_text='Apenas para escalas semanais (0=seg … 6=dom)',
    )

    # Modo de atribuição
    use_team_rotation = models.BooleanField(
        default=False,
        verbose_name='Usar Rotação de Equipes',
        help_text='Se marcado, usa equipes que se revezam. Se não, atribui membros diretamente aos dias.'
    )

    # Orientações
    guidelines = models.TextField(
        blank=True,
        null=True,
        verbose_name='Orientações',
        help_text='Instruções e informações importantes para esta escala'
    )

    # Publicação
    is_published = models.BooleanField(
        default=False,
        verbose_name='Publicada',
        help_text='Se publicada, a escala fica visível para os membros'
    )
    published_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Publicada em'
    )

    class Meta:
        verbose_name = 'Escala'
        verbose_name_plural = 'Escalas'
        ordering = ['-year', '-month', '-start_date', 'ministry__name', 'title']

    def __str__(self):
        if self.schedule_type == self.TYPE_WEEKLY:
            return f"{self.ministry.name} - {self.title} (Semanal)"
        return f"{self.ministry.name} - {self.title} - {self.get_month_display()}/{self.year}"

    @property
    def is_weekly(self):
        return self.schedule_type == self.TYPE_WEEKLY

    @property
    def is_monthly(self):
        return self.schedule_type == self.TYPE_MONTHLY

    def get_days_of_week_list(self):
        """Retorna lista de inteiros dos dias da semana (escala semanal)."""
        if not self.days_of_week:
            return []
        return [int(d.strip()) for d in self.days_of_week.split(',')]

    def set_days_of_week(self, day_list):
        """Define days_of_week a partir de lista de inteiros."""
        self.days_of_week = ','.join(str(d) for d in sorted(day_list)) if day_list else ''

    def get_days_display(self):
        """Retorna string legível dos dias da semana."""
        names = {v: k.split('-')[0] for v, k in [(d[0], d[1]) for d in self.WEEK_DAYS_CHOICES]}
        return ', '.join(names[i] for i in self.get_days_of_week_list() if i in names)

    def get_total_days(self):
        """Retorna total de dias no mês (apenas mensal)."""
        if self.month and self.year:
            return monthrange(self.year, self.month)[1]
        return 0

    def publish(self):
        if not self.is_published:
            self.is_published = True
            self.published_at = datetime.now()
            self.save()

    def unpublish(self):
        if self.is_published:
            self.is_published = False
            self.published_at = None
            self.save()


class ScheduleDay(BaseModel):
    """Dia específico dentro de uma escala (mensal ou semanal)."""

    schedule = models.ForeignKey(
        MonthlySchedule,
        on_delete=models.CASCADE,
        related_name='days',
        verbose_name='Escala'
    )
    date = models.DateField(
        verbose_name='Data'
    )
    # Preenchido apenas em escalas semanais para identificar o padrão de repetição
    day_of_week = models.IntegerField(
        blank=True,
        null=True,
        verbose_name='Dia da Semana',
        help_text='0=seg … 6=dom. Preenchido apenas em escalas semanais.',
    )
    
    # Para escalas com rotação de equipes
    team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='schedule_days',
        verbose_name='Equipe',
        help_text='Equipe escalada para este dia (apenas se usar rotação de equipes)'
    )
    
    # Para escalas com atribuição direta de membros
    members = models.ManyToManyField(
        'Member',
        blank=True,
        related_name='schedule_days',
        verbose_name='Membros',
        help_text='Membros escalados para este dia'
    )

    # Turnos (Manhã / Noite)
    has_shifts = models.BooleanField(
        default=False,
        verbose_name='Tem turnos (Manhã/Noite)',
        help_text='Se verdadeiro, este dia tem escalas separadas para manhã e noite'
    )
    members_morning = models.ManyToManyField(
        'Member',
        blank=True,
        related_name='schedule_days_morning',
        verbose_name='Membros da Manhã',
    )
    members_evening = models.ManyToManyField(
        'Member',
        blank=True,
        related_name='schedule_days_evening',
        verbose_name='Membros da Noite',
    )

    # Informações do dia
    description = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        verbose_name='Descrição',
        help_text='Ex: Culto de Quarta, Culto de Domingo, Ensaio'
    )
    notes = models.TextField(
        blank=True,
        null=True,
        verbose_name='Observações'
    )
    
    # Cancelamento
    is_cancelled = models.BooleanField(
        default=False,
        verbose_name='Cancelado',
        help_text='Marcar se este dia foi cancelado'
    )
    cancellation_reason = models.TextField(
        blank=True,
        null=True,
        verbose_name='Motivo do Cancelamento'
    )
    
    class Meta:
        verbose_name = 'Dia de Escala'
        verbose_name_plural = 'Dias de Escala'
        ordering = ['date']
        unique_together = [['schedule', 'date']]
    
    def __str__(self):
        status = " (CANCELADO)" if self.is_cancelled else ""
        team_or_members = ""
        
        if self.team:
            team_or_members = f" - {self.team.name}"
        elif self.members.exists():
            team_or_members = f" - {self.members.count()} membros"
        
        return f"{self.schedule.title} - {self.date.strftime('%d/%m/%Y')}{team_or_members}{status}"
    
    def get_all_members(self):
        """Retorna todos os membros escalados (da equipe ou diretos)"""
        members = []
        
        # Membros da equipe
        if self.team:
            members.extend(list(self.team.members.filter(is_active=True)))
        
        # Membros individuais
        members.extend(list(self.members.filter(is_active=True)))
        
        # Remover duplicatas mantendo ordem
        seen = set()
        unique_members = []
        for member in members:
            if member.id not in seen:
                seen.add(member.id)
                unique_members.append(member)
        
        return unique_members
    
    def get_week_number(self):
        """Retorna o número da semana no mês (1-5)"""
        return (self.date.day - 1) // 7 + 1


class ScaleDivision(BaseModel):
    """Subdivisão hierárquica dentro de uma escala mensal para organizar os dias.
    Ex: Escala de Louvor → 'Ministro', 'Back Vocal', 'Músicos'
    Profundidade máxima: 3 níveis.
    """

    MAX_DEPTH = 3

    name = models.CharField(
        max_length=100,
        verbose_name='Nome da Divisão'
    )
    schedule = models.ForeignKey(
        MonthlySchedule,
        on_delete=models.CASCADE,
        related_name='divisions',
        verbose_name='Escala'
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children',
        verbose_name='Divisão Pai',
        help_text='Deixe vazio para divisão de nível raiz'
    )
    order = models.IntegerField(
        default=0,
        verbose_name='Ordem',
        help_text='Ordem de exibição (menor = primeiro)'
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name='Ativa'
    )

    class Meta:
        verbose_name = 'Divisão de Escala'
        verbose_name_plural = 'Divisões de Escala'
        ordering = ['schedule', 'order', 'name']
        unique_together = [['schedule', 'name', 'parent']]

    def __str__(self):
        if self.parent:
            return f"{self.schedule.title} → {self.parent.name} → {self.name}"
        return f"{self.schedule.title} → {self.name}"

    def get_depth(self):
        """Retorna a profundidade na hierarquia (0 = raiz)"""
        depth = 0
        current = self
        while current.parent_id:
            depth += 1
            current = current.parent
        return depth

    def clean(self):
        super().clean()
        if self.parent and self.parent.schedule_id != self.schedule_id:
            raise ValidationError(
                'A divisão pai deve pertencer à mesma escala.'
            )
        if self.get_depth() >= self.MAX_DEPTH:
            raise ValidationError(
                f'Profundidade máxima de {self.MAX_DEPTH} níveis atingida.'
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def get_descendants(self):
        """Retorna todas as subdivisões descendentes (filhos, netos, etc.)"""
        descendants = []
        children = ScaleDivision.objects.filter(
            parent=self, deleted__isnull=True
        )
        for child in children:
            descendants.append(child)
            descendants.extend(child.get_descendants())
        return descendants

    def get_ancestors(self):
        """Retorna todos os ancestrais da divisão"""
        ancestors = []
        current = self
        while current.parent_id:
            current = current.parent
            ancestors.append(current)
        return ancestors


class DivisionMember(BaseModel):
    """Atribuição de um membro a uma divisão em um dia específico de escala."""

    SHIFT_NONE = 'none'
    SHIFT_MORNING = 'morning'
    SHIFT_EVENING = 'evening'
    SHIFT_CHOICES = [
        (SHIFT_NONE, 'Sem turno'),
        (SHIFT_MORNING, 'Manhã'),
        (SHIFT_EVENING, 'Noite'),
    ]

    division = models.ForeignKey(
        ScaleDivision,
        on_delete=models.CASCADE,
        related_name='division_members',
        verbose_name='Divisão'
    )
    schedule_day = models.ForeignKey(
        ScheduleDay,
        on_delete=models.CASCADE,
        related_name='division_assignments',
        verbose_name='Dia de Escala'
    )
    member = models.ForeignKey(
        'Member',
        on_delete=models.CASCADE,
        related_name='division_assignments',
        verbose_name='Membro'
    )
    shift = models.CharField(
        max_length=10,
        choices=SHIFT_CHOICES,
        default=SHIFT_NONE,
        verbose_name='Turno',
    )

    class Meta:
        verbose_name = 'Membro da Divisão'
        verbose_name_plural = 'Membros das Divisões'
        unique_together = [['division', 'schedule_day', 'member', 'shift']]
        ordering = ['division__order', 'member__name']

    def __str__(self):
        return f"{self.member.name} - {self.division.name} ({self.schedule_day.date.strftime('%d/%m/%Y')})"

