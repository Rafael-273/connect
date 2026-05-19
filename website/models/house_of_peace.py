from django.db import models
from django.utils import timezone
from ._base import BaseModel
from .member import Member
from .neighborhood import Neighborhood


class HouseOfPeace(BaseModel):
    """
    Represents a family that requested a House of Peace.
    Anyone can register via public form.
    Up to 3 ministry members can accept each House of Peace.
    """

    PRAYER_TYPE_CHOICES = [
        ('family', 'Família em geral'),
        ('health', 'Saúde e cura'),
        ('financial', 'Financeiro'),
        ('relationships', 'Relacionamentos'),
        ('spiritual', 'Crescimento espiritual'),
        ('children', 'Filhos / criação dos filhos'),
        ('marriage', 'Casamento'),
        ('work', 'Trabalho / emprego'),
        ('other', 'Outro'),
    ]

    STATUS_CHOICES = [
        ('available', 'Disponível'),
        ('in_progress', 'Em andamento'),
        ('completed', 'Concluída'),
        ('cancelled', 'Cancelada'),
    ]

    # Dados do solicitante
    family_name = models.CharField(max_length=150, verbose_name='Nome do solicitante')
    phone = models.CharField(max_length=20, verbose_name='Telefone para contato')
    address = models.TextField(verbose_name='Endereço completo')
    neighborhood = models.ForeignKey(
        Neighborhood,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Bairro',
    )

    # Informações sobre a família
    family_size = models.PositiveIntegerField(verbose_name='Número de membros na família')

    # O que deseja oração / mais informações
    prayer_types = models.CharField(
        max_length=500,
        blank=True,
        default='',
        verbose_name='Motivos de oração',
        help_text='Lista separada por vírgulas dos motivos selecionados',
    )
    prayer_description = models.TextField(
        blank=True,
        null=True,
        verbose_name='Descrição / pedido de oração',
        help_text='Descreva com mais detalhes o que deseja oração',
    )



    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available', verbose_name='Status')

    # Membros que aceitaram esta Casa de Paz (máximo 3)
    accepted_members = models.ManyToManyField(
        Member,
        through='HouseOfPeaceAssignment',
        related_name='houses_of_peace',
        blank=True,
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Casa de Paz'
        verbose_name_plural = 'Casas de Paz'

    def __str__(self):
        return f'Casa de Paz - {self.family_name} ({self.neighborhood})'

    @property
    def assignments_count(self):
        return self.assignments.filter(status__in=['accepted', 'scheduled']).count()

    @property
    def active_assignments(self):
        return self.assignments.filter(status__in=['accepted', 'scheduled']).select_related('member')

    @property
    def requester_name(self):
        """Alias para family_name para compatibilidade com templates."""
        return self.family_name

    @property
    def can_accept_more(self):
        """Pode receber mais membros (máximo 3)?"""
        return self.assignments.count() < 3

    @property
    def is_available(self):
        return self.status == 'available'

    @property
    def prayer_types_list(self):
        """Retorna lista dos tipos de oração."""
        if not self.prayer_types:
            return []
        return [t.strip() for t in self.prayer_types.split(',') if t.strip()]

    @property
    def prayer_types_display(self):
        """Retorna nomes legíveis dos tipos de oração."""
        mapping = dict(self.PRAYER_TYPE_CHOICES)
        return [mapping.get(t, t) for t in self.prayer_types_list]


class HouseOfPeaceAssignment(BaseModel):
    """
    Link between a member and a House of Peace.
    Records when the member accepted, the scheduled day, if it was completed, etc.
    """

    STATUS_CHOICES = [
        ('accepted', 'Aceita'),
        ('scheduled', 'Agendada'),
        ('completed', 'Concluída'),
        ('cancelled', 'Cancelada'),
    ]

    house_of_peace = models.ForeignKey(
        HouseOfPeace,
        on_delete=models.CASCADE,
        related_name='assignments',
    )
    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name='house_of_peace_assignments',
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='accepted')
    accepted_at = models.DateTimeField(auto_now_add=True)
    scheduled_date = models.DateField(null=True, blank=True, verbose_name='Dia marcado para a visita')

    # Relatório pós-visita
    completed_at = models.DateTimeField(null=True, blank=True)
    report = models.TextField(blank=True, null=True, verbose_name='Breve relato da visita')

    # Cura
    had_healing = models.BooleanField(
        default=False,
        verbose_name='Houve cura?',
    )
    healing_description = models.TextField(
        blank=True,
        null=True,
        verbose_name='Descrição da cura',
    )

    # Testemunho
    had_testimony = models.BooleanField(
        default=False,
        verbose_name='Houve testemunho?',
    )
    testimony_description = models.TextField(
        blank=True,
        null=True,
        verbose_name='Descrição do testemunho',
    )

    will_continue = models.BooleanField(
        default=False,
        verbose_name='Haverá mais Casas de Paz com essa família?',
    )

    class Meta:
        ordering = ['-accepted_at']
        verbose_name = 'Atribuição de Casa de Paz'
        verbose_name_plural = 'Atribuições de Casa de Paz'
        indexes = [
            models.Index(fields=['house_of_peace', 'member', 'status']),
        ]

    def __str__(self):
        return f'{self.member.name} → {self.house_of_peace.family_name}'

    def complete(self, report, had_healing, healing_desc, had_testimony, testimony_desc, will_continue):
        self.status = 'completed'
        self.completed_at = timezone.now()
        self.report = report
        self.had_healing = had_healing
        self.healing_description = healing_desc or None
        self.had_testimony = had_testimony
        self.testimony_description = testimony_desc or None
        self.will_continue = will_continue
        self.save()

        # Check if all assignments are completed to close the House of Peace
        house = self.house_of_peace
        if house.assignments.filter(status='completed').count() >= house.assignments.count():
            house.status = 'completed'
            house.save()
