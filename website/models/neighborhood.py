from django.db import models
from ._base import BaseModel


class Neighborhood(BaseModel):
    name = models.CharField(max_length=100)
    parent = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='subneighborhoods')

    def __str__(self):
        return self.name
