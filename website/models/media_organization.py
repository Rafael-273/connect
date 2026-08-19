from django.db import models

from ._base import BaseModel


LEADERSHIP_CATEGORY_CHOICES = [
    ('improvement', 'Melhoria necessária'),
    ('problem', 'Problema identificado'),
    ('idea', 'Ideia'),
    ('process', 'Processo a criar'),
    ('equipment', 'Equipamento necessário'),
    ('training', 'Treinamento necessário'),
    ('adjustment', 'Ajuste futuro'),
    ('internal_project', 'Projeto interno'),
]

LEADERSHIP_PRIORITY_CHOICES = [
    ('low', 'Baixa'),
    ('medium', 'Média'),
    ('high', 'Alta'),
    ('critical', 'Crítica'),
]

LEADERSHIP_STATUS_CHOICES = [
    ('open', 'Aberto'),
    ('in_progress', 'Em andamento'),
    ('done', 'Concluído'),
    ('cancelled', 'Cancelado'),
]

RESOURCE_CATEGORY_CHOICES = [
    ('social', 'Rede social'),
    ('email', 'E-mail'),
    ('design', 'Design / Criação'),
    ('video', 'Vídeo / Edição'),
    ('storage', 'Armazenamento'),
    ('analytics', 'Analytics / Ads'),
    ('website', 'Site'),
    ('other', 'Outro'),
]

SUBSCRIPTION_TYPE_CHOICES = [
    ('free', 'Gratuito'),
    ('paid', 'Pago'),
    ('church', 'Conta da igreja'),
    ('personal', 'Conta pessoal compartilhada'),
    ('trial', 'Período de teste'),
]


class MediaSubTeam(BaseModel):
    """Subequipe dentro do Ministério de Mídia Externa."""

    name = models.CharField(max_length=100, unique=True, verbose_name='Nome')
    description = models.TextField(blank=True, verbose_name='Descrição')
    leader = models.ForeignKey(
        'Member',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='media_subteams_led',
        verbose_name='Responsável / líder',
    )
    responsibilities = models.TextField(blank=True, verbose_name='Responsabilidades da equipe')
    notes = models.TextField(blank=True, verbose_name='Observações')
    is_active = models.BooleanField(default=True, verbose_name='Ativa')

    class Meta:
        ordering = ['name']
        verbose_name = 'Subequipe de Mídia'
        verbose_name_plural = 'Subequipes de Mídia'

    def __str__(self):
        return self.name


class MediaRole(BaseModel):
    """Função reutilizável (ex: Operador de câmera, Editor)."""

    name = models.CharField(max_length=100, verbose_name='Nome')
    description = models.TextField(blank=True, verbose_name='Descrição')
    sub_team = models.ForeignKey(
        MediaSubTeam,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='roles',
        verbose_name='Equipe relacionada',
    )

    class Meta:
        ordering = ['name']
        unique_together = [('name', 'sub_team')]
        verbose_name = 'Função de Mídia'
        verbose_name_plural = 'Funções de Mídia'

    def __str__(self):
        if self.sub_team_id:
            return f'{self.name} ({self.sub_team.name})'
        return self.name


class MediaSubTeamMembership(BaseModel):
    """Participação de um membro em uma subequipe de mídia."""

    sub_team = models.ForeignKey(
        MediaSubTeam,
        on_delete=models.CASCADE,
        related_name='memberships',
        verbose_name='Equipe',
    )
    member = models.ForeignKey(
        'Member',
        on_delete=models.CASCADE,
        related_name='media_subteam_memberships',
        verbose_name='Membro',
    )
    roles = models.ManyToManyField(
        MediaRole,
        blank=True,
        related_name='memberships',
        verbose_name='Funções',
    )
    responsibilities = models.TextField(blank=True, verbose_name='Responsabilidades')
    is_active = models.BooleanField(default=True, verbose_name='Ativo')

    class Meta:
        unique_together = [('sub_team', 'member')]
        verbose_name = 'Membro de Subequipe'
        verbose_name_plural = 'Membros de Subequipe'

    def __str__(self):
        return f'{self.member.name} — {self.sub_team.name}'


class MediaLeadershipItem(BaseModel):
    """Item estratégico para acompanhamento da liderança de mídia."""

    title = models.CharField(max_length=200, verbose_name='Título')
    description = models.TextField(blank=True, verbose_name='Descrição')
    category = models.CharField(
        max_length=30,
        choices=LEADERSHIP_CATEGORY_CHOICES,
        verbose_name='Categoria',
    )
    priority = models.CharField(
        max_length=10,
        choices=LEADERSHIP_PRIORITY_CHOICES,
        default='medium',
        verbose_name='Prioridade',
    )
    status = models.CharField(
        max_length=20,
        choices=LEADERSHIP_STATUS_CHOICES,
        default='open',
        verbose_name='Status',
    )
    responsible = models.ForeignKey(
        'Member',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='media_leadership_items',
        verbose_name='Responsável',
    )
    notes = models.TextField(blank=True, verbose_name='Observações')

    class Meta:
        ordering = ['-priority', '-created_at']
        verbose_name = 'Item de Liderança (Mídia)'
        verbose_name_plural = 'Itens de Liderança (Mídia)'

    def __str__(self):
        return self.title


class MediaResource(BaseModel):
    """Recurso ou acesso compartilhado do ministério de mídia."""

    name = models.CharField(max_length=150, verbose_name='Nome')
    category = models.CharField(
        max_length=20,
        choices=RESOURCE_CATEGORY_CHOICES,
        verbose_name='Categoria',
    )
    url = models.URLField(blank=True, verbose_name='URL')
    email_username = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='E-mail / usuário',
    )
    responsible = models.ForeignKey(
        'Member',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='media_resources_managed',
        verbose_name='Responsável',
    )
    access_members = models.ManyToManyField(
        'Member',
        blank=True,
        related_name='media_resources_access',
        verbose_name='Quem possui acesso',
    )
    subscription_type = models.CharField(
        max_length=20,
        choices=SUBSCRIPTION_TYPE_CHOICES,
        blank=True,
        verbose_name='Tipo de assinatura',
    )
    renewal_date = models.DateField(
        null=True,
        blank=True,
        verbose_name='Data de renovação',
    )
    notes = models.TextField(blank=True, verbose_name='Observações')
    is_active = models.BooleanField(default=True, verbose_name='Ativo')

    class Meta:
        ordering = ['name']
        verbose_name = 'Recurso de Mídia'
        verbose_name_plural = 'Recursos de Mídia'

    def __str__(self):
        return self.name

    @property
    def has_credentials(self):
        return hasattr(self, 'credential') and bool(self.credential.encrypted_password)


class MediaResourceCredential(BaseModel):
    """Credenciais criptografadas de um recurso (cofre separado)."""

    resource = models.OneToOneField(
        MediaResource,
        on_delete=models.CASCADE,
        related_name='credential',
        verbose_name='Recurso',
    )
    encrypted_password = models.TextField(blank=True, verbose_name='Senha (criptografada)')
    encrypted_notes = models.TextField(blank=True, verbose_name='Notas sensíveis (criptografadas)')
    last_updated_by = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='media_credential_updates',
        verbose_name='Atualizado por',
    )

    class Meta:
        verbose_name = 'Credencial de Recurso'
        verbose_name_plural = 'Credenciais de Recursos'

    def __str__(self):
        return f'Credencial: {self.resource.name}'


class MediaCredentialAccessLog(BaseModel):
    """Auditoria de acesso a credenciais."""

    ACTION_CHOICES = [
        ('view', 'Visualização'),
        ('update', 'Atualização'),
    ]

    credential = models.ForeignKey(
        MediaResourceCredential,
        on_delete=models.CASCADE,
        related_name='access_logs',
        verbose_name='Credencial',
    )
    user = models.ForeignKey(
        'User',
        on_delete=models.CASCADE,
        related_name='media_credential_logs',
        verbose_name='Usuário',
    )
    action = models.CharField(max_length=10, choices=ACTION_CHOICES, verbose_name='Ação')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Log de Acesso a Credencial'
        verbose_name_plural = 'Logs de Acesso a Credenciais'

    def __str__(self):
        return f'{self.user} — {self.get_action_display()} — {self.credential}'
