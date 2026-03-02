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
    """Escala mensal de um ministério"""
    
    MONTH_CHOICES = [
        (1, 'Janeiro'), (2, 'Fevereiro'), (3, 'Março'), (4, 'Abril'),
        (5, 'Maio'), (6, 'Junho'), (7, 'Julho'), (8, 'Agosto'),
        (9, 'Setembro'), (10, 'Outubro'), (11, 'Novembro'), (12, 'Dezembro')
    ]
    
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
    month = models.IntegerField(
        choices=MONTH_CHOICES,
        verbose_name='Mês'
    )
    year = models.IntegerField(
        verbose_name='Ano'
    )
    
    
    # Modo de atribuição
    use_team_rotation = models.BooleanField(
        default=False,
        verbose_name='Usar Rotação de Equipes',
        help_text='Se marcado, usa equipes que se revezam por semana. Se não, atribui membros diretamente aos dias.'
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
        verbose_name = 'Escala Mensal'
        verbose_name_plural = 'Escalas Mensais'
        ordering = ['-year', '-month', 'ministry__name', 'title']
        unique_together = [['ministry', 'title', 'month', 'year']]
    
    def __str__(self):
        return f"{self.ministry.name} - {self.title} - {self.get_month_display()}/{self.year}"
    
    def get_total_days(self):
        """Retorna total de dias no mês"""
        return monthrange(self.year, self.month)[1]
    
    def publish(self):
        """Publica a escala"""
        if not self.is_published:
            self.is_published = True
            self.published_at = datetime.now()
            self.save()
    
    def unpublish(self):
        """Despublica a escala"""
        if self.is_published:
            self.is_published = False
            self.published_at = None
            self.save()


class ScheduleDay(BaseModel):
    """Dia específico dentro de uma escala mensal"""
    
    schedule = models.ForeignKey(
        MonthlySchedule,
        on_delete=models.CASCADE,
        related_name='days',
        verbose_name='Escala'
    )
    date = models.DateField(
        verbose_name='Data'
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
    """Subdivisão hierárquica dentro de um ministério para organizar escalas.
    Ex: Ministério de Música → 'Ministro', 'Back Vocal', 'Músicos'
    Profundidade máxima: 3 níveis.
    """

    MAX_DEPTH = 3

    name = models.CharField(
        max_length=100,
        verbose_name='Nome da Divisão'
    )
    ministry = models.ForeignKey(
        'Ministry',
        on_delete=models.CASCADE,
        related_name='divisions',
        verbose_name='Ministério'
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
        ordering = ['ministry', 'order', 'name']
        unique_together = [['ministry', 'name', 'parent']]

    def __str__(self):
        if self.parent:
            return f"{self.ministry.name} → {self.parent.name} → {self.name}"
        return f"{self.ministry.name} → {self.name}"

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
        if self.parent and self.parent.ministry_id != self.ministry_id:
            raise ValidationError(
                'A divisão pai deve pertencer ao mesmo ministério.'
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

    class Meta:
        verbose_name = 'Membro da Divisão'
        verbose_name_plural = 'Membros das Divisões'
        unique_together = [['division', 'schedule_day', 'member']]
        ordering = ['division__order', 'member__name']

    def __str__(self):
        return f"{self.member.name} - {self.division.name} ({self.schedule_day.date.strftime('%d/%m/%Y')})"

