from django.db import models

from ._base import BaseModel


class ProsperarCompany(BaseModel):
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pendente de aprovacao'),
        (STATUS_APPROVED, 'Aprovada'),
        (STATUS_REJECTED, 'Rejeitada'),
    ]

    BUSINESS_SECTOR_FOOD = 'food'
    BUSINESS_SECTOR_BEAUTY = 'beauty'
    BUSINESS_SECTOR_HEALTH = 'health'
    BUSINESS_SECTOR_EDUCATION = 'education'
    BUSINESS_SECTOR_TECH = 'tech'
    BUSINESS_SECTOR_FASHION = 'fashion'
    BUSINESS_SECTOR_CONSTRUCTION = 'construction'
    BUSINESS_SECTOR_AUTOMOTIVE = 'automotive'
    BUSINESS_SECTOR_FINANCE = 'finance'
    BUSINESS_SECTOR_HOME = 'home'
    BUSINESS_SECTOR_SERVICES = 'services'
    BUSINESS_SECTOR_OTHER = 'other'

    BUSINESS_SECTOR_CHOICES = [
        (BUSINESS_SECTOR_FOOD, 'Alimentacao'),
        (BUSINESS_SECTOR_BEAUTY, 'Beleza e estetica'),
        (BUSINESS_SECTOR_HEALTH, 'Saude e bem-estar'),
        (BUSINESS_SECTOR_EDUCATION, 'Educacao'),
        (BUSINESS_SECTOR_TECH, 'Tecnologia'),
        (BUSINESS_SECTOR_FASHION, 'Moda e acessorios'),
        (BUSINESS_SECTOR_CONSTRUCTION, 'Construcao e reformas'),
        (BUSINESS_SECTOR_AUTOMOTIVE, 'Automotivo'),
        (BUSINESS_SECTOR_FINANCE, 'Financas e consultoria'),
        (BUSINESS_SECTOR_HOME, 'Casa e decoracao'),
        (BUSINESS_SECTOR_SERVICES, 'Servicos gerais'),
        (BUSINESS_SECTOR_OTHER, 'Outro'),
    ]

    user = models.OneToOneField(
        'User',
        on_delete=models.CASCADE,
        related_name='prosperar_company',
    )
    member = models.ForeignKey(
        'Member',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='prosperar_companies',
        verbose_name='Membro vinculado',
    )
    company_name = models.CharField(max_length=180, verbose_name='Nome da empresa')
    business_sector = models.CharField(
        max_length=30,
        choices=BUSINESS_SECTOR_CHOICES,
        verbose_name='Ramo de atividade',
    )
    neighborhood = models.ForeignKey(
        'Neighborhood',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='prosperar_companies',
        verbose_name='Bairro',
    )
    logo = models.ImageField(
        upload_to='prosperar_logos/',
        blank=True,
        null=True,
        verbose_name='Logo da empresa',
    )
    address = models.TextField(verbose_name='Endereco')
    phone = models.CharField(max_length=20, blank=True, verbose_name='Telefone / WhatsApp')
    description = models.TextField(blank=True, verbose_name='Descricao')
    instagram_url = models.URLField(blank=True, verbose_name='Link do Instagram')
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        verbose_name='Status',
    )
    is_featured = models.BooleanField(default=False, verbose_name='Empresa em destaque')

    class Meta:
        verbose_name = 'Empresa Prosperar'
        verbose_name_plural = 'Empresas Prosperar'
        ordering = ['company_name']

    def __str__(self):
        return self.company_name
