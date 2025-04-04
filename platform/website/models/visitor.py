from django.db import models
from ._base import BaseModel
from .member import Member
from .neighborhood import Neighborhood


class Visitor(BaseModel):
    CONVERSION_CHOICES = Member.CONVERSION_CHOICES
    GENDER_CHOICES = Member.GENDER_CHOICES

    name = models.CharField(max_length=150)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    neighborhood = models.ForeignKey(Neighborhood, on_delete=models.SET_NULL, blank=True, null=True)
    visit_date = models.DateField(auto_now_add=True)
    decision_for_jesus = models.BooleanField(default=False)
    conversion = models.CharField(max_length=20, choices=CONVERSION_CHOICES)
    prayer_request = models.TextField(blank=True, null=True)
    profile_notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name