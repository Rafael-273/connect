from django.db import models
from django.utils import timezone
from zoneinfo import ZoneInfo
from ._base import BaseModel
from .member import Member
from .neighborhood import Neighborhood


def visitor_local_date():
    return timezone.localdate(timezone=ZoneInfo('America/Sao_Paulo'))


class Visitor(BaseModel):
    CONVERSION_CHOICES = Member.CONVERSION_CHOICES
    GENDER_CHOICES = Member.GENDER_CHOICES
    COMPANION_CHOICES = [
        ('family', 'Família'),
        ('alone', 'Sozinho'),
        ('young', 'Jovem'),
        ('teenager', 'Adolescente'),
    ]

    name = models.CharField(max_length=150)
    email = models.EmailField(unique=True, blank=True, null=True)
    birth_date = models.DateField(blank=True, null=True, verbose_name='Data de Nascimento')
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    neighborhood = models.ForeignKey(Neighborhood, on_delete=models.SET_NULL, blank=True, null=True)
    visit_date = models.DateField(default=visitor_local_date)
    decision_for_jesus = models.BooleanField(default=False)
    conversion = models.CharField(max_length=20, choices=CONVERSION_CHOICES, null=True, blank=True)
    prayer_request = models.TextField(blank=True, null=True)
    profile_notes = models.TextField(blank=True, null=True)
    companion = models.CharField(
        max_length=20,
        choices=COMPANION_CHOICES,
        blank=True,
        verbose_name='Visitante veio com',
    )
    wants_home_prayer = models.BooleanField(default=False, verbose_name='Deseja oração na casa?')
    wants_house_of_peace = models.BooleanField(default=False, verbose_name='Tem interesse em Casa de Paz?')

    def __str__(self):
        return self.name
