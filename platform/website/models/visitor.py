from django.db import models
from ._base import BaseModel
from .member import Member


class Visitor(models.Model):
    CONVERSION_CHOICES = Member.CONVERSION_CHOICES

    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    visit_date = models.DateField(auto_now_add=True)
    conversion = models.CharField(max_length=20, choices=CONVERSION_CHOICES)
    prayer_request = models.TextField(blank=True, null=True)
    qr_code = models.ImageField(upload_to="qrcodes/", blank=True, null=True)

    def __str__(self):
        return self.name