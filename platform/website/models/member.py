from django.db import models
from ._base import BaseModel
from .ministry import Ministry


class Member(BaseModel):
    CONVERSION_CHOICES = [
        ('new_convert', 'Novo Convertido'),
        ('reconciled', 'Reconciliado'),
        ('from_another_church', 'Vindo de outra Igreja'),
    ]

    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    birth_date = models.DateField(blank=True, null=True)
    is_admin = models.BooleanField(default=False)
    conversion = models.CharField(max_length=20, choices=CONVERSION_CHOICES, blank=True, null=True)
    ministry = models.ForeignKey(Ministry, on_delete=models.SET_NULL, blank=True, null=True)
    prayer_request = models.TextField(blank=True, null=True)
    
    is_consolidator = models.BooleanField(default=False)
    is_consolidated = models.BooleanField(default=False)
    is_discipler = models.BooleanField(default=False)
    is_discipled = models.BooleanField(default=False)

    def __str__(self):
        return self.name